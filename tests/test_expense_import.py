"""Tests for the bootstrap budget import against the unified dinary.duckdb."""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import allure
import pytest

from dinary import config
from dinary.config import ImportSourceRow
from dinary.imports import expense_import
from dinary.imports.expense_import import import_year
from dinary.services import duckdb_repo


@pytest.fixture(autouse=True)
def _tmp_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(duckdb_repo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(duckdb_repo, "DB_PATH", tmp_path / "dinary.duckdb")


@pytest.fixture(autouse=True)
def _stub_import_sources(monkeypatch):
    """Stand in for ``.deploy/import_sources.json`` without touching disk.

    Patches the loader in ``dinary.config`` so every caller
    (``expense_import`` imports ``get_import_source`` by name) sees
    the same single-row fixture. Keeps the test hermetic even when a
    developer has a real ``.deploy/import_sources.json`` locally.
    """
    rows = [
        ImportSourceRow(
            year=2026,
            spreadsheet_id="sheet-id",
            worksheet_name="",
            layout_key="default",
        ),
    ]
    monkeypatch.setattr(config, "read_import_sources", lambda: list(rows))


def _seed_catalog():
    """Seed the minimum catalog needed by ``import_year(2026)``.

    ``import_year`` requires per-year synthetic vacation and business
    trip events, plus the data literals ``"поездка в Россию"`` (Russia
    trip) and ``"релокация-в-Сербию"`` — the importer looks them up by
    name.
    """
    duckdb_repo.init_db()
    con = duckdb_repo.get_connection()
    try:
        con.execute(
            "INSERT INTO category_groups (id, name, sort_order, is_active)"
            " VALUES (1, 'g', 1, TRUE)",
        )
        con.execute(
            "INSERT INTO categories (id, name, group_id, is_active) VALUES (1, 'еда', 1, TRUE)",
        )
        con.execute(
            "INSERT INTO categories (id, name, group_id, is_active)"
            " VALUES (2, 'мобильник', 1, TRUE)",
        )
        con.execute(
            "INSERT INTO categories (id, name, group_id, is_active) VALUES (3, 'кафе', 1, TRUE)",
        )
        con.execute(
            "INSERT INTO tags (id, name, is_active) VALUES (1, 'собака', TRUE)",
        )
        con.execute(
            "INSERT INTO events (id, name, date_from, date_to, auto_attach_enabled)"
            " VALUES (1, 'отпуск-2026', '2026-01-01', '2026-12-31', TRUE)",
        )
        # One-off event normally seeded by ``EXPLICIT_EVENTS``;
        # ``import_year`` looks it up by name for the 2026 fix.
        con.execute(
            "INSERT INTO events (id, name, date_from, date_to, auto_attach_enabled)"
            " VALUES (2, 'поездка в Россию', '2026-08-01', '2026-08-31', FALSE)",
        )
        con.execute(
            "INSERT INTO import_mapping (id, year, sheet_category, sheet_group,"
            " category_id, event_id) VALUES (1, 0, 'еда', 'собака', 1, NULL)",
        )
        con.execute("INSERT INTO import_mapping_tags VALUES (1, 1)")
        con.execute(
            "INSERT INTO import_mapping (id, year, sheet_category, sheet_group,"
            " category_id, event_id) VALUES (2, 0, 'мобильник', '', 2, NULL)",
        )
        con.execute(
            "INSERT INTO import_mapping (id, year, sheet_category, sheet_group,"
            " category_id, event_id) VALUES (3, 0, 'кафе', 'путешествия', 3, 1)",
        )
    finally:
        con.close()


SHEET_ROWS = [
    ["Date", "RSD", "EUR", "Category", "Group", "Comment", "Month", "Rate"],
    ["2026-01-01", "4500", "", "еда", "собака", "lunch", "1", "117"],
    ["2026-01-01", "400", "", "мобильник", "", "", "1", "117"],
    ["2026-01-01", "2000", "", "кафе", "путешествия", "resort", "1", "117"],
    ["2026-02-01", "1500", "", "еда", "собака", "snack", "2", "117"],
]


def _mock_sheet():
    ws = MagicMock()
    ws.get_all_values.return_value = SHEET_ROWS
    ss = MagicMock()
    ss.sheet1 = ws
    ss.worksheet.return_value = ws
    return ss


def _mock_prefetch_rates(_year, _layout, *, con=None):
    one_to_one = {
        "rate_src": Decimal("1"),
        "rate_eur": Decimal("1"),
        "rate_acc": Decimal("1"),
    }
    return {m: one_to_one for m in range(1, 13)}


@allure.epic("Import")
@allure.feature("Bootstrap (3D)")
class TestImportYear:
    @patch(
        "dinary.imports.expense_import._prefetch_monthly_rates",
        side_effect=_mock_prefetch_rates,
    )
    @patch("dinary.imports.expense_import.get_sheet")
    def test_imports_rows_with_3d_dimensions(self, mock_sheet, _mr):
        _seed_catalog()
        mock_sheet.return_value = _mock_sheet()

        result = import_year(2026)

        assert result["expenses_created"] == 4
        assert result["errors"] == 0

        con = duckdb_repo.get_connection()
        try:
            rows = con.execute(
                "SELECT category_id, event_id, sheet_category, sheet_group,"
                " client_expense_id FROM expenses ORDER BY datetime, sheet_category",
            ).fetchall()
        finally:
            con.close()
        assert len(rows) == 4
        for cat_id, _ev_id, sheet_cat, sheet_grp, client_id in rows:
            assert cat_id is not None
            assert sheet_cat is not None
            assert sheet_grp is not None
            # Bootstrap rows do not carry a PWA-generated idempotency key.
            assert client_id is None

    @patch(
        "dinary.imports.expense_import._prefetch_monthly_rates",
        side_effect=_mock_prefetch_rates,
    )
    @patch("dinary.imports.expense_import.get_sheet")
    def test_attaches_tags_from_mapping(self, mock_sheet, _mr):
        _seed_catalog()
        mock_sheet.return_value = _mock_sheet()
        import_year(2026)

        con = duckdb_repo.get_connection()
        try:
            tag_rows = con.execute(
                "SELECT t.tag_id FROM expense_tags t"
                " JOIN expenses e ON e.id = t.expense_id"
                " WHERE e.sheet_category = 'еда'",
            ).fetchall()
        finally:
            con.close()
        assert {r[0] for r in tag_rows} == {1}

    @patch(
        "dinary.imports.expense_import._prefetch_monthly_rates",
        side_effect=_mock_prefetch_rates,
    )
    @patch("dinary.imports.expense_import.get_sheet")
    def test_does_not_enqueue_logging_jobs(self, mock_sheet, _mr):
        _seed_catalog()
        mock_sheet.return_value = _mock_sheet()
        import_year(2026)

        con = duckdb_repo.get_connection()
        try:
            assert duckdb_repo.list_logging_jobs(con) == []
        finally:
            con.close()

    @patch(
        "dinary.imports.expense_import._prefetch_monthly_rates",
        side_effect=_mock_prefetch_rates,
    )
    @patch("dinary.imports.expense_import.get_sheet")
    def test_re_import_is_destructive(self, mock_sheet, _mr):
        _seed_catalog()
        mock_sheet.return_value = _mock_sheet()
        first = import_year(2026)
        second = import_year(2026)
        assert first["expenses_created"] == second["expenses_created"]

        # NULL client_expense_id is legal multiple times; the re-import
        # wipes YEAR(datetime)=year rows first so we still end up with
        # exactly four expenses, not eight.
        con = duckdb_repo.get_connection()
        try:
            total = con.execute(
                "SELECT COUNT(*) FROM expenses WHERE YEAR(datetime) = 2026",
            ).fetchone()[0]
        finally:
            con.close()
        assert total == 4

    @patch(
        "dinary.imports.expense_import._prefetch_monthly_rates",
        side_effect=_mock_prefetch_rates,
    )
    @patch("dinary.imports.expense_import.get_sheet")
    def test_re_import_with_pending_logging_jobs_does_not_violate_fk(
        self,
        mock_sheet,
        _mr,
    ):
        """If ``sheet_logging_jobs`` has pending rows from a prior runtime
        session, ``DELETE FROM expenses`` would fail with an FK violation
        (queue rows FK into ``expenses.id`` with no ON DELETE CASCADE).
        The fix deletes queue rows for *year* first."""
        _seed_catalog()
        mock_sheet.return_value = _mock_sheet()
        import_year(2026)

        con = duckdb_repo.get_connection()
        try:
            existing_id = con.execute(
                "SELECT id FROM expenses WHERE YEAR(datetime) = 2026 LIMIT 1",
            ).fetchone()[0]
            con.execute(
                "INSERT INTO sheet_logging_jobs (expense_id, status) VALUES (?, 'pending')",
                [existing_id],
            )
        finally:
            con.close()

        result = import_year(2026)
        assert result["errors"] == 0
        con = duckdb_repo.get_connection()
        try:
            assert duckdb_repo.list_logging_jobs(con) == []
        finally:
            con.close()

    @patch(
        "dinary.imports.expense_import._prefetch_monthly_rates",
        side_effect=_mock_prefetch_rates,
    )
    @patch("dinary.imports.expense_import.get_sheet")
    def test_resolve_dimensions_raise_skips_row_instead_of_aborting(
        self,
        mock_sheet,
        _mr,
        monkeypatch,
    ):
        """When ``_resolve_dimensions`` raises (e.g. a tag goes missing
        on the no-mapping fallback), the exception is caught per-row so
        the rest of the sheet still imports."""
        _seed_catalog()
        mock_sheet.return_value = _mock_sheet()

        real_resolve = expense_import._resolve_dimensions
        call_state = {"n": 0}

        def flaky_resolve(*args, **kwargs):
            call_state["n"] += 1
            if call_state["n"] == 1:
                msg = "tag 'phantom' not found in tags; re-seed required"
                raise ValueError(msg)
            return real_resolve(*args, **kwargs)

        monkeypatch.setattr(expense_import, "_resolve_dimensions", flaky_resolve)

        result = import_year(2026)
        assert result["errors"] == 1
        # Four rows in SHEET_ROWS; the first fails resolution, the other
        # three succeed.
        assert result["expenses_created"] == 3
