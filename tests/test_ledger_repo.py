"""Tests for the ledger repository layer (``ledger_repo``).

All tests point ``ledger_repo.DATA_DIR`` / ``DB_PATH`` at a per-test
``tmp_path`` and run ``init_db()`` up-front so each test operates on
a clean ``dinary.db`` file.
"""

import sqlite3
from datetime import datetime, timedelta

import allure
import pytest

from dinary.services import ledger_repo


@pytest.fixture(autouse=True)
def _tmp_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger_repo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ledger_repo, "DB_PATH", tmp_path / "dinary.db")


@pytest.fixture
def fresh_db():
    ledger_repo.init_db()


@pytest.fixture
def populated_catalog(fresh_db):
    """Seed the catalog with a minimal 3D dataset."""
    con = ledger_repo.get_connection()
    try:
        con.execute(
            "INSERT INTO category_groups (id, name, sort_order, is_active)"
            " VALUES (1, 'Food', 1, 1)",
        )
        con.execute(
            "INSERT INTO categories (id, name, group_id, is_active) VALUES (1, 'еда', 1, 1)",
        )
        con.execute(
            "INSERT INTO categories (id, name, group_id, is_active) VALUES (2, 'кафе', 1, 1)",
        )
        con.execute(
            "INSERT INTO events (id, name, date_from, date_to,"
            " auto_attach_enabled, is_active)"
            " VALUES (10, 'отпуск-2026', '2026-01-01', '2026-12-31', 1, 1)",
        )
        con.execute("INSERT INTO tags (id, name, is_active) VALUES (1, 'собака', 1)")
        con.execute(
            "INSERT INTO tags (id, name, is_active) VALUES (2, 'релокация', 1)",
        )
        con.execute(
            "INSERT INTO import_mapping (id, year, sheet_category, sheet_group,"
            " category_id, event_id) VALUES (1, 0, 'еда', 'собака', 1, NULL)",
        )
        con.execute(
            "INSERT INTO import_mapping_tags (mapping_id, tag_id) VALUES (1, 1)",
        )
        con.execute(
            "INSERT INTO import_mapping (id, year, sheet_category, sheet_group,"
            " category_id, event_id) VALUES (2, 0, 'кафе', 'путешествия', 2, 10)",
        )
        con.execute(
            "INSERT INTO import_mapping (id, year, sheet_category, sheet_group,"
            " category_id, event_id) VALUES (3, 2026, 'еда', 'собака', 2, 10)",
        )
        con.commit()
    finally:
        con.close()


@allure.epic("Ledger repo")
@allure.feature("Connection lifecycle")
class TestConnectionLifecycle:
    def test_init_creates_file(self, tmp_path):
        assert not ledger_repo.DB_PATH.exists()
        ledger_repo.init_db()
        assert ledger_repo.DB_PATH.exists()

    def test_get_connection_before_init_autocreates(self, tmp_path):
        """``get_connection`` opens the file even without explicit init."""
        con = ledger_repo.get_connection()
        try:
            row = con.execute("SELECT 1").fetchone()
        finally:
            con.close()
        assert row == (1,)

    def test_close_releases_singleton(self, fresh_db):
        # ``close_connection`` is a post-SQLite-port no-op retained for
        # test-harness compatibility; ``get_connection`` always returns
        # a fresh connection after the port. The test still exercises
        # the call to pin the no-op contract.
        ledger_repo.close_connection()
        con = ledger_repo.get_connection()
        try:
            con.execute("SELECT 1").fetchone()
        finally:
            con.close()

    def test_multiple_connections_see_each_others_commits(self, fresh_db):
        """Two independent connections observe each other's commits.

        After the SQLite port ``get_connection`` hands out one fresh
        connection per call (no shared engine). WAL mode plus an
        explicit commit on the writer lets the second connection
        observe the new row.
        """
        c1 = ledger_repo.get_connection()
        c2 = ledger_repo.get_connection()
        try:
            c1.execute(
                "INSERT INTO category_groups (id, name, sort_order) VALUES (42, 'g42', 99)",
            )
            c1.commit()
            row = c2.execute(
                "SELECT name FROM category_groups WHERE id = 42",
            ).fetchone()
        finally:
            c1.close()
            c2.close()
        assert row == ("g42",)


@allure.epic("Ledger repo")
@allure.feature("Catalog version")
class TestCatalogVersion:
    def test_initial_version_is_one(self, fresh_db):
        con = ledger_repo.get_connection()
        try:
            assert ledger_repo.get_catalog_version(con) == 1
        finally:
            con.close()

    def test_set_then_get(self, fresh_db):
        con = ledger_repo.get_connection()
        try:
            ledger_repo.set_catalog_version(con, 42)
            assert ledger_repo.get_catalog_version(con) == 42
        finally:
            con.close()

    def test_missing_key_raises(self, fresh_db):
        con = ledger_repo.get_connection()
        try:
            con.execute("DELETE FROM app_metadata WHERE key = 'catalog_version'")
            with pytest.raises(RuntimeError, match="catalog_version"):
                ledger_repo.get_catalog_version(con)
        finally:
            con.close()


@allure.epic("Ledger repo")
@allure.feature("List categories (is_active)")
class TestListCategories:
    def test_filters_inactive_rows(self, fresh_db):
        con = ledger_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order, is_active)"
                " VALUES (1, 'Food', 1, 1), (2, 'Gone', 2, 0)",
            )
            con.execute(
                "INSERT INTO categories (id, name, group_id, is_active)"
                " VALUES (1, 'active', 1, 1),"
                " (2, 'retired', 1, 0),"
                " (3, 'orphan-group', 2, 1)",
            )
            rows = ledger_repo.list_categories(con)
        finally:
            con.close()
        names = {r.name for r in rows}
        # 'retired' filtered out (is_active=false on category); 'orphan-group'
        # filtered out (group is_active=false).
        assert names == {"active"}

    def test_ordering(self, fresh_db):
        con = ledger_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order, is_active)"
                " VALUES (1, 'Z', 2, 1), (2, 'A', 1, 1)",
            )
            con.execute(
                "INSERT INTO categories (id, name, group_id, is_active)"
                " VALUES (1, 'b', 1, 1),"
                " (2, 'a', 1, 1),"
                " (3, 'c', 2, 1)",
            )
            rows = ledger_repo.list_categories(con)
        finally:
            con.close()
        # Groups ordered by sort_order: 'A' (1) before 'Z' (2); within each
        # group, categories ordered by name ascending.
        assert [(r.name, r.group_name) for r in rows] == [
            ("c", "A"),
            ("a", "Z"),
            ("b", "Z"),
        ]


@allure.epic("Ledger repo")
@allure.feature("Sheet mapping (3D)")
class TestSheetMapping:
    def test_resolve_year_zero_default(self, populated_catalog):
        con = ledger_repo.get_connection()
        try:
            row = ledger_repo.resolve_mapping(con, "еда", "собака")
            assert row is not None
            assert row.category_id == 1
            assert row.event_id is None
        finally:
            con.close()

    def test_year_specific_overrides_default(self, populated_catalog):
        con = ledger_repo.get_connection()
        try:
            row = ledger_repo.resolve_mapping_for_year(con, "еда", "собака", 2026)
            assert row is not None
            assert row.category_id == 2
            assert row.event_id == 10
        finally:
            con.close()

    def test_year_falls_back_to_zero(self, populated_catalog):
        con = ledger_repo.get_connection()
        try:
            row = ledger_repo.resolve_mapping_for_year(con, "еда", "собака", 2024)
            assert row is not None
            assert row.category_id == 1
        finally:
            con.close()

    def test_unknown_returns_none(self, populated_catalog):
        con = ledger_repo.get_connection()
        try:
            assert ledger_repo.resolve_mapping(con, "missing", "?") is None
        finally:
            con.close()

    def test_get_mapping_tag_ids(self, populated_catalog):
        con = ledger_repo.get_connection()
        try:
            assert ledger_repo.get_mapping_tag_ids(con, 1) == [1]
            assert ledger_repo.get_mapping_tag_ids(con, 2) == []
        finally:
            con.close()


@allure.epic("Ledger repo")
@allure.feature("Logging projection (sheet_mapping)")
class TestLoggingProjection:
    @pytest.fixture
    def logging_setup(self, fresh_db):
        """Seed one category with three ``sheet_mapping`` rows exercising
        the "first non-``*`` wins per column" resolver:

        row_order=1: category=еда, tag ``tag1`` required, Расходы=``*``,
                     Конверт=``WithTag``
        row_order=2: category=еда, event=``evt``, Расходы=``*``,
                     Конверт=``WithEvt``
        row_order=3: category=еда (catch-all), Расходы=``CatA``,
                     Конверт=``*``
        """
        con = ledger_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order) VALUES (1, 'g', 1)",
            )
            con.execute(
                "INSERT INTO categories (id, name, group_id) VALUES (1, 'еда', 1)",
            )
            con.execute("INSERT INTO tags (id, name) VALUES (1, 'tag1')")
            con.execute("INSERT INTO tags (id, name) VALUES (2, 'tag2')")
            con.execute(
                "INSERT INTO events (id, name, date_from, date_to, auto_attach_enabled)"
                " VALUES (1, 'evt', '2026-01-01', '2026-12-31', 1)",
            )
            con.execute(
                "INSERT INTO sheet_mapping (row_order, category_id, event_id,"
                " sheet_category, sheet_group) VALUES (1, 1, NULL, '*', 'WithTag')",
            )
            con.execute("INSERT INTO sheet_mapping_tags VALUES (1, 1)")
            con.execute(
                "INSERT INTO sheet_mapping (row_order, category_id, event_id,"
                " sheet_category, sheet_group) VALUES (2, 1, 1, '*', 'WithEvt')",
            )
            con.execute(
                "INSERT INTO sheet_mapping (row_order, category_id, event_id,"
                " sheet_category, sheet_group) VALUES (3, 1, NULL, 'CatA', '*')",
            )
            con.commit()
        finally:
            con.close()

    def test_event_row_wins_конверт(self, logging_setup):
        con = ledger_repo.get_connection()
        try:
            # No tags: row_order=1 skipped; row_order=2 matches on event_id
            # and fills Конверт=WithEvt; row_order=3 supplies Расходы=CatA.
            result = ledger_repo.logging_projection(
                con,
                category_id=1,
                event_id=1,
                tag_ids=[],
            )
            assert result == ("CatA", "WithEvt")
        finally:
            con.close()

    def test_tag_row_wins_конверт(self, logging_setup):
        con = ledger_repo.get_connection()
        try:
            # No event, tag 'tag1' present: row_order=1 fills Конверт=WithTag;
            # row_order=3 supplies Расходы=CatA.
            result = ledger_repo.logging_projection(
                con,
                category_id=1,
                event_id=None,
                tag_ids=[1],
            )
            assert result == ("CatA", "WithTag")
        finally:
            con.close()

    def test_partial_resolution_keeps_resolved_column(self, logging_setup):
        con = ledger_repo.get_connection()
        try:
            # tag 'tag2' is not required by any row, no event: rows 1 and 2
            # are skipped, row 3 fills Расходы=CatA but leaves Конверт as
            # ``*``. The resolver keeps the partial match and fills the
            # missing ``sheet_group`` with the empty-string fallback —
            # dropping CatA would be strictly worse since we already
            # picked a better-than-default value for that column.
            result = ledger_repo.logging_projection(
                con,
                category_id=1,
                event_id=None,
                tag_ids=[2],
            )
            assert result == ("CatA", "")
        finally:
            con.close()

    def test_unknown_category_returns_none(self, logging_setup):
        con = ledger_repo.get_connection()
        try:
            # category_id=999 has no rows at all and no canonical name —
            # the projection returns None so the drain worker can poison
            # the queued row rather than logging to a bogus target.
            result = ledger_repo.logging_projection(
                con,
                category_id=999,
                event_id=None,
                tag_ids=[],
            )
            assert result is None
        finally:
            con.close()

    def test_no_event_no_tags_fills_envelope_with_empty_string(self, logging_setup):
        con = ledger_repo.get_connection()
        try:
            # Same shape as ``test_partial_resolution_keeps_resolved_column``
            # but with no tags at all; rows 1 and 2 require tag/event and
            # are skipped, row 3 assigns Расходы and leaves Конверт as
            # ``*``. The resolver fills Конверт with the empty-string
            # fallback while keeping the explicit ``CatA`` mapping.
            result = ledger_repo.logging_projection(
                con,
                category_id=1,
                event_id=None,
                tag_ids=[],
            )
            assert result == ("CatA", "")
        finally:
            con.close()

    def test_no_mapping_rule_falls_back_to_category_name(self, fresh_db):
        """When no sheet_mapping rule fires at all, both columns fall
        back: ``sheet_category`` = categories.name, ``sheet_group`` = ''.
        This replaces the old "return None and let the caller fall
        back" contract with an in-helper default so partial matches
        never get discarded.
        """
        con = ledger_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order) VALUES (1, 'g', 1)",
            )
            con.execute(
                "INSERT INTO categories (id, name, group_id) VALUES (1, 'еда', 1)",
            )
            con.commit()
            assert ledger_repo.logging_projection(
                con,
                category_id=1,
                event_id=None,
                tag_ids=[],
            ) == ("еда", "")
        finally:
            con.close()

    def test_both_columns_resolved_when_wildcard_row_fills_конверт(self, fresh_db):
        """A dedicated envelope-fill row + a category row together resolve
        both columns and produce a non-None result."""
        con = ledger_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order) VALUES (1, 'g', 1)",
            )
            con.execute(
                "INSERT INTO categories (id, name, group_id) VALUES (1, 'еда', 1)",
            )
            con.execute(
                "INSERT INTO sheet_mapping (row_order, category_id, event_id,"
                " sheet_category, sheet_group)"
                " VALUES (1, NULL, NULL, '*', 'envelope')",
            )
            con.execute(
                "INSERT INTO sheet_mapping (row_order, category_id, event_id,"
                " sheet_category, sheet_group)"
                " VALUES (2, 1, NULL, 'CatA', '*')",
            )
            con.commit()
            assert ledger_repo.logging_projection(
                con,
                category_id=1,
                event_id=None,
                tag_ids=[],
            ) == ("CatA", "envelope")
        finally:
            con.close()

    def test_event_wildcard_row_matches_specific_event(self, fresh_db):
        con = ledger_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order) VALUES (1, 'g', 1)",
            )
            con.execute(
                "INSERT INTO categories (id, name, group_id) VALUES (1, 'еда', 1)",
            )
            con.execute(
                "INSERT INTO events (id, name, date_from, date_to,"
                " auto_attach_enabled) VALUES (1, 'отпуск-2026',"
                " '2026-01-01', '2026-04-20', 1)",
            )
            # Row 1 (specific event) must win over the wildcard row for
            # event_id=1; row 2 must still fire for event_id=None.
            # ``sheet_group`` is left wildcard and therefore falls back
            # to the empty-string sentinel in both cases.
            con.execute(
                "INSERT INTO sheet_mapping (row_order, category_id, event_id,"
                " sheet_category, sheet_group)"
                " VALUES (1, 1, 1, 'Trips', '*')",
            )
            con.execute(
                "INSERT INTO sheet_mapping (row_order, category_id, event_id,"
                " sheet_category, sheet_group)"
                " VALUES (2, 1, NULL, 'Default', '*')",
            )
            con.commit()
            assert ledger_repo.logging_projection(
                con,
                category_id=1,
                event_id=1,
                tag_ids=[],
            ) == ("Trips", "")
            assert ledger_repo.logging_projection(
                con,
                category_id=1,
                event_id=None,
                tag_ids=[],
            ) == ("Default", "")
        finally:
            con.close()


@allure.epic("Ledger repo")
@allure.feature("get_category_name")
class TestGetCategoryName:
    def test_existing(self, fresh_db):
        con = ledger_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order) VALUES (1, 'g', 1)",
            )
            con.execute(
                "INSERT INTO categories (id, name, group_id) VALUES (1, 'еда', 1)",
            )
            con.commit()
            assert ledger_repo.get_category_name(con, 1) == "еда"
        finally:
            con.close()

    def test_missing(self, fresh_db):
        con = ledger_repo.get_connection()
        try:
            assert ledger_repo.get_category_name(con, 999) is None
        finally:
            con.close()


@allure.epic("Ledger repo")
@allure.feature("Insert expense (client_expense_id)")
class TestInsertExpense:
    def test_insert_then_duplicate(self, populated_catalog):
        con = ledger_repo.get_connection()
        try:
            r1 = ledger_repo.insert_expense(
                con,
                client_expense_id="x1",
                expense_datetime=datetime(2026, 5, 5, 12),
                amount=10.0,
                amount_original=10.0,
                currency_original="EUR",
                category_id=1,
                event_id=None,
                comment="hi",
                sheet_category=None,
                sheet_group=None,
                tag_ids=[1],
                enqueue_logging=False,
            )
            r2 = ledger_repo.insert_expense(
                con,
                client_expense_id="x1",
                expense_datetime=datetime(2026, 5, 5, 12),
                amount=10.0,
                amount_original=10.0,
                currency_original="EUR",
                category_id=1,
                event_id=None,
                comment="hi",
                sheet_category=None,
                sheet_group=None,
                tag_ids=[1],
                enqueue_logging=False,
            )
            assert r1 == "created"
            assert r2 == "duplicate"
        finally:
            con.close()

    def test_conflict_on_changed_amount(self, populated_catalog):
        con = ledger_repo.get_connection()
        try:
            ledger_repo.insert_expense(
                con,
                client_expense_id="x2",
                expense_datetime=datetime(2026, 5, 5, 12),
                amount=10.0,
                amount_original=10.0,
                currency_original="EUR",
                category_id=1,
                comment="",
                tag_ids=[],
                enqueue_logging=False,
            )
            r = ledger_repo.insert_expense(
                con,
                client_expense_id="x2",
                expense_datetime=datetime(2026, 5, 5, 12),
                amount=99.0,
                amount_original=99.0,
                currency_original="EUR",
                category_id=1,
                comment="",
                tag_ids=[],
                enqueue_logging=False,
            )
            assert r == "conflict"
        finally:
            con.close()

    def test_invalid_category_raises(self, populated_catalog):
        con = ledger_repo.get_connection()
        try:
            with pytest.raises(ValueError, match="category_id"):
                ledger_repo.insert_expense(
                    con,
                    client_expense_id="x3",
                    expense_datetime=datetime(2026, 5, 5, 12),
                    amount=1.0,
                    amount_original=1.0,
                    currency_original="EUR",
                    category_id=9999,
                    comment="",
                    tag_ids=[],
                )
        finally:
            con.close()

    def test_invalid_provenance_pair_raises(self, populated_catalog):
        con = ledger_repo.get_connection()
        try:
            with pytest.raises(ValueError, match="sheet_category"):
                ledger_repo.insert_expense(
                    con,
                    client_expense_id="x4",
                    expense_datetime=datetime(2026, 5, 5, 12),
                    amount=1.0,
                    amount_original=1.0,
                    currency_original="EUR",
                    category_id=1,
                    comment="",
                    sheet_category="X",
                    sheet_group=None,
                    tag_ids=[],
                )
        finally:
            con.close()

    def test_enqueue_logging_creates_pending_row(self, populated_catalog):
        con = ledger_repo.get_connection()
        try:
            ledger_repo.insert_expense(
                con,
                client_expense_id="x5",
                expense_datetime=datetime(2026, 5, 5, 12),
                amount=1.0,
                amount_original=1.0,
                currency_original="EUR",
                category_id=1,
                comment="",
                tag_ids=[],
                enqueue_logging=True,
            )
            jobs = ledger_repo.list_logging_jobs(con)
        finally:
            con.close()
        # Integer expense PKs in the queue.
        assert len(jobs) == 1
        assert isinstance(jobs[0], int)

    def test_null_client_id_allows_multiple(self, populated_catalog):
        """Bootstrap rows use ``client_expense_id=None`` and must coexist."""
        con = ledger_repo.get_connection()
        try:
            r1 = ledger_repo.insert_expense(
                con,
                client_expense_id=None,
                expense_datetime=datetime(2026, 1, 1, 12),
                amount=1.0,
                amount_original=1.0,
                currency_original="EUR",
                category_id=1,
                comment="legacy-1",
                sheet_category="cat",
                sheet_group="grp",
                tag_ids=[],
                enqueue_logging=False,
            )
            r2 = ledger_repo.insert_expense(
                con,
                client_expense_id=None,
                expense_datetime=datetime(2026, 1, 2, 12),
                amount=2.0,
                amount_original=2.0,
                currency_original="EUR",
                category_id=1,
                comment="legacy-2",
                sheet_category="cat",
                sheet_group="grp",
                tag_ids=[],
                enqueue_logging=False,
            )
            assert r1 == "created"
            assert r2 == "created"
            count = con.execute("SELECT COUNT(*) FROM expenses").fetchone()[0]
            assert count == 2
        finally:
            con.close()

    def test_unique_client_id_raises_constraint(self, populated_catalog):
        """Direct SQL violation — the wrapper catches and returns
        duplicate/conflict, but the DB enforces UNIQUE regardless."""
        con = ledger_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO expenses (client_expense_id, datetime, amount,"
                " amount_original, currency_original, category_id)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                ["u1", datetime(2026, 1, 1, 12), 1, 1, "EUR", 1],
            )
            con.commit()
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(
                    "INSERT INTO expenses (client_expense_id, datetime, amount,"
                    " amount_original, currency_original, category_id)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    ["u1", datetime(2026, 1, 1, 12), 1, 1, "EUR", 1],
                )
        finally:
            con.close()


@allure.epic("Ledger repo")
@allure.feature("insert_expense race-recovery branch")
class TestInsertExpenseRaceRecovery:
    """Exercise the ``_RACE_EXCS`` branch inside ``insert_expense``.

    The production SQL (``sql/insert_expense.sql``) uses
    ``ON CONFLICT (client_expense_id) DO NOTHING`` so SQLite silently
    absorbs conflicts with already-committed winners — the
    ``except _RACE_EXCS`` branch never fires on the happy path, and
    the end-to-end concurrency tests in ``tests/test_api.py`` now
    assert that the ``race_counter`` stays at zero under SQLite's
    serialized-writer model.

    That leaves the recovery branch uncovered by regular tests even
    though it stays in the source as a defensive net for any future
    writer module that drops ``ON CONFLICT`` (or for a raw SQL path
    that bubbles the IntegrityError up). This test forces the branch
    by swapping ``load_sql("insert_expense.sql")`` for a bare INSERT
    (no ``ON CONFLICT``) for the duration of the second call, so the
    same code path gets to observe a real ``sqlite3.IntegrityError``
    from a real duplicate key and must land in the compare-path.

    Paired with ``TestIsUniqueViolationOfClientExpenseId`` (which
    pins the message-matching classifier) and the ``sql/insert_expense``
    file itself, this keeps every leg of the race path exercised
    against a real SQLite engine.
    """

    def _insert_winner(self, populated_catalog) -> None:
        """Commit the original ``client_expense_id='race-x'`` row."""
        con = ledger_repo.get_connection()
        try:
            result = ledger_repo.insert_expense(
                con,
                client_expense_id="race-x",
                expense_datetime=datetime(2026, 5, 5, 12),
                amount=10.0,
                amount_original=10.0,
                currency_original="EUR",
                category_id=1,
                comment="winner",
                tag_ids=[1],
                enqueue_logging=False,
            )
            assert result == "created"
        finally:
            con.close()

    def _bare_insert_sql(self) -> str:
        """Replacement SQL with no ``ON CONFLICT`` clause.

        Mirrors ``sql/insert_expense.sql`` column shape so
        ``insert_expense`` binds the same 10 parameters in the same
        order; only the conflict-absorption clause is dropped so the
        second call to ``con.execute`` actually raises ``IntegrityError``.
        """
        return (
            "INSERT INTO expenses (client_expense_id, datetime, amount,"
            " amount_original, currency_original, category_id, event_id,"
            " comment, sheet_category, sheet_group)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " RETURNING id"
        )

    def test_duplicate_on_bare_insert_falls_through_to_compare_path(
        self, populated_catalog, monkeypatch
    ):
        """Identical second insert → ``IntegrityError`` → recovery → ``duplicate``."""
        self._insert_winner(populated_catalog)

        original_load_sql = ledger_repo.load_sql
        bare_sql = self._bare_insert_sql()

        def fake_load_sql(name: str) -> str:
            if name == "insert_expense.sql":
                return bare_sql
            return original_load_sql(name)

        monkeypatch.setattr(ledger_repo, "load_sql", fake_load_sql)

        con = ledger_repo.get_connection()
        try:
            result = ledger_repo.insert_expense(
                con,
                client_expense_id="race-x",
                expense_datetime=datetime(2026, 5, 5, 12),
                amount=10.0,
                amount_original=10.0,
                currency_original="EUR",
                category_id=1,
                comment="winner",
                tag_ids=[1],
                enqueue_logging=False,
            )
        finally:
            con.close()

        assert result == "duplicate"

    def test_conflict_on_bare_insert_falls_through_to_compare_path(
        self, populated_catalog, monkeypatch
    ):
        """Different amount on second insert → ``IntegrityError`` → recovery → ``conflict``."""
        self._insert_winner(populated_catalog)

        original_load_sql = ledger_repo.load_sql
        bare_sql = self._bare_insert_sql()

        def fake_load_sql(name: str) -> str:
            if name == "insert_expense.sql":
                return bare_sql
            return original_load_sql(name)

        monkeypatch.setattr(ledger_repo, "load_sql", fake_load_sql)

        con = ledger_repo.get_connection()
        try:
            result = ledger_repo.insert_expense(
                con,
                client_expense_id="race-x",
                expense_datetime=datetime(2026, 5, 5, 12),
                amount=99.0,
                amount_original=99.0,
                currency_original="EUR",
                category_id=1,
                comment="loser",
                tag_ids=[1],
                enqueue_logging=False,
            )
        finally:
            con.close()

        assert result == "conflict"


@allure.epic("Ledger repo")
@allure.feature("Race-recovery exception classification")
class TestIsUniqueViolationOfClientExpenseId:
    """Pin the classifier behind ``insert_expense``'s race recovery.

    ``_is_unique_violation_of_client_expense_id`` is the *only* gate
    between "fall through to the compare path" (serve
    duplicate/conflict) and "propagate as 500" (unknown DB failure)
    for the concurrent-POST paths. It decides by pattern-matching the
    engine's English error messages, so a quiet diagnostic-text change
    in a future engine release could silently flip every unique race
    into a 500. These tests freeze the real SQLite error strings from
    the version this code targets so the classifier's contract stays
    observable in CI.

    Paired with
    ``TestInsertExpense::test_unique_client_id_raises_constraint``
    (which pins that the *exception classes* SQLite raises stay the
    set we catch), this covers the full input surface of the recovery
    path without needing to fabricate synthetic exceptions.
    """

    def _real_duplicate_key_exception(self) -> sqlite3.Error:
        """Provoke a real ``IntegrityError`` from SQLite so we freeze
        its actual wording, not a hand-crafted message we invented.
        Uses a throwaway in-memory DB so it doesn't touch the per-test
        tmp DB at all.

        The schema mirrors the ``expenses.client_expense_id`` column
        so the resulting message contains both ``UNIQUE constraint
        failed`` and ``expenses.client_expense_id`` — the two tokens
        the classifier keys on.
        """
        con = sqlite3.connect(":memory:")
        try:
            con.execute(
                "CREATE TABLE expenses (client_expense_id TEXT UNIQUE, v INTEGER)",
            )
            con.execute("INSERT INTO expenses VALUES ('k1', 1)")
            try:
                con.execute("INSERT INTO expenses VALUES ('k1', 2)")
            except sqlite3.Error as exc:
                return exc
            msg = "SQLite unexpectedly accepted a duplicate key"
            raise AssertionError(msg)
        finally:
            con.close()

    def _real_fk_violation_exception(self) -> sqlite3.Error:
        """Same idea for the FK path — catch the real SQLite wording
        so we're classifier-against-truth, not
        classifier-against-stub. The classifier's ``"foreign key"``
        carve-out is the discriminator: SQLite's FK violation message
        carries that phrase and none of the duplicate-key positive
        keywords.
        """
        con = sqlite3.connect(":memory:")
        con.execute("PRAGMA foreign_keys=ON")
        try:
            con.execute("CREATE TABLE p (id INTEGER PRIMARY KEY)")
            con.execute(
                "CREATE TABLE c (pid INTEGER REFERENCES p(id))",
            )
            try:
                con.execute("INSERT INTO c VALUES (42)")
            except sqlite3.Error as exc:
                return exc
            msg = "SQLite unexpectedly accepted an FK violation"
            raise AssertionError(msg)
        finally:
            con.close()

    def test_classifies_real_duplicate_key_as_race(self):
        exc = self._real_duplicate_key_exception()
        assert ledger_repo._is_unique_violation_of_client_expense_id(exc), (
            f"SQLite dup-key message no longer matches classifier keywords: {exc!s}"
        )

    def test_classifies_real_fk_violation_as_not_a_race(self):
        exc = self._real_fk_violation_exception()
        assert not ledger_repo._is_unique_violation_of_client_expense_id(exc), (
            f"FK violation was misclassified as a client_expense_id "
            f"UNIQUE race; would be swallowed as a duplicate/conflict "
            f"response instead of surfacing as 500. Message: {exc!s}"
        )

    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            # SQLite's canonical dup-key wording; the only form currently
            # produced by the CPython stdlib binding we target.
            (
                "UNIQUE constraint failed: expenses.client_expense_id",
                True,
            ),
            # FK variants must not match even when other tables' unique
            # constraints appear in the same batch — the ``foreign key``
            # carve-out kicks in as soon as the phrase is present.
            (
                "FOREIGN KEY constraint failed",
                False,
            ),
            # Unique-constraint violations on *other* columns must not
            # be misclassified as the client_expense_id race.
            (
                "UNIQUE constraint failed: expenses.id",
                False,
            ),
            (
                "UNIQUE constraint failed: tags.name",
                False,
            ),
            # Unrelated failures must pass through untouched.
            ("disk I/O error", False),
            ("database is locked", False),
        ],
    )
    def test_fixed_message_matrix(self, message: str, expected: bool):
        """Explicit matrix pinning the classifier against a mix of
        unique / FK / unrelated-failure wordings. Any keyword
        rearrangement in the classifier (dropping the table-qualified
        column match, dropping the ``foreign key`` carve-out, etc.)
        flips at least one row here.
        """
        fake = RuntimeError(message)
        assert ledger_repo._is_unique_violation_of_client_expense_id(fake) is expected, message


@allure.epic("Ledger repo")
@allure.feature("lookup_existing_expense")
class TestLookupExistingExpense:
    def test_found(self, populated_catalog):
        con = ledger_repo.get_connection()
        try:
            ledger_repo.insert_expense(
                con,
                client_expense_id="L1",
                expense_datetime=datetime(2026, 4, 15, 12),
                amount=42.0,
                amount_original=42.0,
                currency_original="EUR",
                category_id=1,
                comment="lunch",
                tag_ids=[],
                enqueue_logging=False,
            )
        finally:
            con.close()

        row = ledger_repo.lookup_existing_expense("L1")
        assert row is not None
        assert row.currency_original == "EUR"
        assert row.category_id == 1
        assert row.comment == "lunch"

    def test_not_found(self, populated_catalog):
        assert ledger_repo.lookup_existing_expense("missing") is None


@allure.epic("Ledger repo")
@allure.feature("sheet_logging_jobs queue")
class TestLoggingQueue:
    def _insert_one_expense(self) -> int:
        con = ledger_repo.get_connection()
        try:
            ledger_repo.insert_expense(
                con,
                client_expense_id="job1",
                expense_datetime=datetime(2026, 1, 1, 12),
                amount=1.0,
                amount_original=1.0,
                currency_original="EUR",
                category_id=1,
                comment="",
                tag_ids=[],
                enqueue_logging=True,
            )
            pk_row = con.execute(
                "SELECT id FROM expenses WHERE client_expense_id = 'job1'",
            ).fetchone()
        finally:
            con.close()
        return int(pk_row[0])

    def test_claim_then_clear(self, populated_catalog):
        pk = self._insert_one_expense()
        con = ledger_repo.get_connection()
        try:
            token = ledger_repo.claim_logging_job(con, pk)
            assert token is not None
            assert ledger_repo.clear_logging_job(con, pk, token) is True
            assert ledger_repo.list_logging_jobs(con) == []
        finally:
            con.close()

    def test_clear_with_wrong_token_returns_false(self, populated_catalog):
        pk = self._insert_one_expense()
        con = ledger_repo.get_connection()
        try:
            ledger_repo.claim_logging_job(con, pk)
            assert ledger_repo.clear_logging_job(con, pk, "wrongtoken") is False
        finally:
            con.close()

    def test_release_returns_to_pending(self, populated_catalog):
        pk = self._insert_one_expense()
        con = ledger_repo.get_connection()
        try:
            token = ledger_repo.claim_logging_job(con, pk)
            assert ledger_repo.release_logging_claim(con, pk, token) is True
            row = con.execute(
                "SELECT status, claim_token FROM sheet_logging_jobs WHERE expense_id = ?",
                [pk],
            ).fetchone()
            assert row[0] == "pending"
            assert row[1] is None
        finally:
            con.close()

    def test_double_claim_blocked(self, populated_catalog):
        pk = self._insert_one_expense()
        con = ledger_repo.get_connection()
        try:
            t1 = ledger_repo.claim_logging_job(con, pk)
            t2 = ledger_repo.claim_logging_job(con, pk)
            assert t1 is not None
            assert t2 is None
        finally:
            con.close()

    def test_stale_claim_recoverable(self, populated_catalog):
        pk = self._insert_one_expense()
        con = ledger_repo.get_connection()
        try:
            now = datetime(2026, 1, 1, 12)
            t1 = ledger_repo.claim_logging_job(con, pk, now=now)
            assert t1 is not None
            future = now + timedelta(hours=1)
            t2 = ledger_repo.claim_logging_job(
                con,
                pk,
                now=future,
                stale_before=future - timedelta(minutes=5),
            )
            assert t2 is not None
            assert t2 != t1
        finally:
            con.close()

    def test_list_filters_fresh_in_progress_but_resurfaces_stale(
        self,
        populated_catalog,
    ):
        """``list_logging_jobs`` must:
        - include ``pending`` rows
        - exclude ``in_progress`` rows whose claim is newer than
          ``stale_before`` (a recently-claimed row belongs to the
          drain that claimed it; skipping avoids burning a
          BEGIN/COMMIT on every iteration for the same row)
        - include ``in_progress`` rows whose claim is older than
          ``stale_before`` (that drain died; the row must surface
          again so the next drain can recover it)
        """
        pk = self._insert_one_expense()
        con = ledger_repo.get_connection()
        try:
            now = datetime(2026, 1, 1, 12)
            token = ledger_repo.claim_logging_job(con, pk, now=now)
            assert token is not None

            # Fresh claim (1 minute old) with a 5-minute cutoff → filtered out.
            fresh_now = now + timedelta(minutes=1)
            fresh_cutoff = fresh_now - timedelta(minutes=5)
            assert (
                ledger_repo.list_logging_jobs(
                    con,
                    now=fresh_now,
                    stale_before=fresh_cutoff,
                )
                == []
            )

            # Stale claim (10 minutes old) with a 5-minute cutoff → resurfaces.
            stale_now = now + timedelta(minutes=10)
            stale_cutoff = stale_now - timedelta(minutes=5)
            assert ledger_repo.list_logging_jobs(
                con,
                now=stale_now,
                stale_before=stale_cutoff,
            ) == [pk]
        finally:
            con.close()

    def test_poison_marks_row(self, populated_catalog):
        pk = self._insert_one_expense()
        con = ledger_repo.get_connection()
        try:
            ledger_repo.poison_logging_job(con, pk, "boom")
            row = con.execute(
                "SELECT status, last_error FROM sheet_logging_jobs WHERE expense_id = ?",
                [pk],
            ).fetchone()
            # Poisoned rows are excluded from list_logging_jobs().
            assert ledger_repo.list_logging_jobs(con) == []
        finally:
            con.close()
        assert row == ("poisoned", "boom")

    def test_force_clear_wipes_row(self, populated_catalog):
        pk = self._insert_one_expense()
        con = ledger_repo.get_connection()
        try:
            assert ledger_repo.force_clear_logging_job(con, pk) is True
            assert ledger_repo.count_logging_jobs(con) == 0
            # Already gone — idempotent false on re-delete.
            assert ledger_repo.force_clear_logging_job(con, pk) is False
        finally:
            con.close()


@allure.epic("Ledger repo")
@allure.feature("get_expense_by_id")
class TestGetExpenseById:
    def test_roundtrip(self, populated_catalog):
        con = ledger_repo.get_connection()
        try:
            ledger_repo.insert_expense(
                con,
                client_expense_id="E1",
                expense_datetime=datetime(2026, 3, 3, 12),
                amount=1.5,
                amount_original=1.5,
                currency_original="EUR",
                category_id=1,
                comment="c",
                tag_ids=[],
                enqueue_logging=False,
            )
            pk = con.execute(
                "SELECT id FROM expenses WHERE client_expense_id = 'E1'",
            ).fetchone()[0]
            row = ledger_repo.get_expense_by_id(con, int(pk))
        finally:
            con.close()
        assert row is not None
        assert row.category_id == 1
        assert row.currency_original == "EUR"
        assert row.comment == "c"

    def test_missing_returns_none(self, populated_catalog):
        con = ledger_repo.get_connection()
        try:
            assert ledger_repo.get_expense_by_id(con, 99999) is None
        finally:
            con.close()
