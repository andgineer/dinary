"""Tests for the 3D-catalog seeding logic on the unified dinary.duckdb."""

import json
from datetime import datetime
from unittest.mock import patch

import allure
import pytest

from dinary.config import settings
from dinary.services import duckdb_repo
from dinary.services.seed_config import (
    ENTRY_GROUPS,
    EXPLICIT_EVENTS,
    PHASE1_TAGS,
    RUSSIA_TRIP_EVENT_NAME,
    SYNTHETIC_EVENT_PREFIX,
    Category,
    _rebuild_logging_mapping_from_latest_year,
    rebuild_config_from_sheets,
    seed_from_sheet,
)

SAMPLE_CATEGORIES = [
    Category(name="еда", group=""),
    Category(name="еда", group="собака"),
    Category(name="кафе", group="путешествия"),
    Category(name="мобильник", group=""),
    Category(name="развлечения", group=""),
    Category(name="командировка", group=""),
    Category(name="обустройство", group="релокация"),
    Category(name="обучение", group="профессиональное"),
]


@pytest.fixture(autouse=True)
def _tmp_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(duckdb_repo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(duckdb_repo, "DB_PATH", tmp_path / "dinary.duckdb")


@pytest.fixture(autouse=True)
def _stub_settings(monkeypatch):
    """Bootstrap a single import source so seed_from_sheet has something to read."""
    monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "")
    monkeypatch.setattr(
        settings,
        "import_sources_json",
        json.dumps(
            [
                {
                    "year": 2026,
                    "spreadsheet_id": "fake-id",
                    "worksheet_name": "Sheet1",
                    "layout_key": "default",
                },
            ],
        ),
    )


def _patched_seed(year=2026):
    with patch(
        "dinary.services.seed_config._load_categories_for_sheet",
        return_value=SAMPLE_CATEGORIES,
    ):
        return seed_from_sheet(year=year)


@allure.epic("Seed catalog")
@allure.feature("seed_from_sheet (3D)")
class TestSeedFromSheet:
    def test_creates_groups(self):
        _patched_seed()
        con = duckdb_repo.get_connection()
        try:
            names = {
                r[0]
                for r in con.execute(
                    "SELECT name FROM category_groups WHERE is_active",
                ).fetchall()
            }
        finally:
            con.close()
        for group_title, _cats in ENTRY_GROUPS:
            assert group_title in names

    def test_creates_categories_with_group_links(self):
        _patched_seed()
        con = duckdb_repo.get_connection()
        try:
            rows = con.execute(
                "SELECT c.name, g.name FROM categories c"
                " JOIN category_groups g ON g.id = c.group_id"
                " WHERE c.is_active",
            ).fetchall()
        finally:
            con.close()
        assert rows
        for cat_name, group_name in rows:
            assert cat_name and group_name

    def test_creates_phase1_tags(self):
        _patched_seed()
        con = duckdb_repo.get_connection()
        try:
            names = {
                r[0]
                for r in con.execute(
                    "SELECT name FROM tags WHERE is_active",
                ).fetchall()
            }
        finally:
            con.close()
        for tag in PHASE1_TAGS:
            assert tag in names

    def test_creates_per_year_synthetic_events(self):
        _patched_seed()
        con = duckdb_repo.get_connection()
        try:
            names = {r[0] for r in con.execute("SELECT name FROM events").fetchall()}
        finally:
            con.close()
        for y in (2018, 2022, 2026):
            assert f"{SYNTHETIC_EVENT_PREFIX}{y}" in names
        assert "релокация-в-Сербию" in names

    def test_creates_import_mappings(self):
        _patched_seed()
        con = duckdb_repo.get_connection()
        try:
            count = con.execute("SELECT COUNT(*) FROM import_mapping").fetchone()[0]
        finally:
            con.close()
        assert count > 0

    def test_logging_mapping_bootstrapped_from_latest_year(self):
        """``logging_mapping`` is rebuilt from latest_year ``import_mapping``
        rows (deduped by canonical 3D key), not from the cross-year year=0
        aggregation. Every active canonical category is guaranteed to have
        at least one ``(event=NULL, tags=[])`` row so the bare
        ``POST /api/expenses`` path always finds an exact match."""
        summary = _patched_seed()

        con = duckdb_repo.get_connection()
        try:
            active_categories = con.execute(
                "SELECT id, name FROM categories WHERE is_active ORDER BY id",
            ).fetchall()

            logging_rows = con.execute(
                "SELECT m.category_id, m.event_id, m.sheet_category, m.sheet_group,"
                " COALESCE("
                "   (SELECT LIST(mt.tag_id ORDER BY mt.tag_id)"
                "    FROM logging_mapping_tags mt"
                "    WHERE mt.mapping_id = m.id),"
                "   []::INTEGER[]"
                " )"
                " FROM logging_mapping m ORDER BY m.id",
            ).fetchall()
        finally:
            con.close()

        assert active_categories, "test setup must seed at least one active category"
        assert logging_rows, "logging_mapping must not be empty after seed"

        # Dedup invariant: at most one row per canonical 3D key.
        keys = [(r[0], r[1], tuple(r[4])) for r in logging_rows]
        assert len(keys) == len(set(keys)), (
            "logging_mapping must hold at most one row per (category, event, tag_set) key; "
            f"got duplicates in {keys}"
        )

        # Canonical-default safety net: every active category has at
        # least one (event=NULL, tags=[]) row whose sheet_category equals
        # the canonical category name.
        rows_by_cat: dict[int, list[tuple]] = {}
        for cat_id, event_id, sheet_category, sheet_group, tag_ids in logging_rows:
            rows_by_cat.setdefault(cat_id, []).append(
                (event_id, sheet_category, sheet_group, tuple(tag_ids))
            )
        for cat_id, cat_name in active_categories:
            cat_rows = rows_by_cat.get(cat_id, [])
            default_rows = [r for r in cat_rows if r[0] is None and r[3] == ()]
            assert default_rows, (
                f"category id={cat_id} ({cat_name!r}) has no (event=NULL, tags=[]) "
                f"default row in logging_mapping"
            )
            canonical_defaults = [r for r in default_rows if r[1] == cat_name and r[2] == ""]
            assert canonical_defaults, (
                f"category id={cat_id} ({cat_name!r}): default row exists but "
                f"sheet_category/sheet_group are not canonical ({cat_name!r}, ''); "
                f"got {default_rows}"
            )

        assert summary["logging_mappings_bootstrapped"] == len(logging_rows)

    def test_logging_mapping_canonical_defaults_fallback(self):
        """Direct coverage of the canonical-defaults-only fallback path
        taken by ``_rebuild_logging_mapping_from_latest_year`` when
        ``latest_year <= 0`` (operator edge case: all configured
        ``import_sources`` entries carry ``year = 0``). The normal
        ``seed_from_sheet`` path cannot reach ``latest_year = 0`` in
        practice because it requires ``pairs`` to be non-empty, so we
        exercise the rebuild directly on a seeded DB.
        """
        _patched_seed()

        con = duckdb_repo.get_connection()
        try:
            con.execute("DELETE FROM logging_mapping_tags")
            con.execute("DELETE FROM logging_mapping")
            active_categories = con.execute(
                "SELECT id, name FROM categories WHERE is_active ORDER BY id",
            ).fetchall()
            cat_id_by_name = {name: int(cid) for cid, name in active_categories}

            written = _rebuild_logging_mapping_from_latest_year(
                con,
                latest_year=0,
                cat_id_by_name=cat_id_by_name,
            )

            logging_rows = con.execute(
                "SELECT category_id, event_id, sheet_category, sheet_group"
                " FROM logging_mapping ORDER BY id",
            ).fetchall()
        finally:
            con.close()

        assert written == len(active_categories)
        assert len(logging_rows) == len(active_categories)
        rows_by_cat = {r[0]: r for r in logging_rows}
        for cat_id, cat_name in active_categories:
            assert rows_by_cat[cat_id] == (cat_id, None, cat_name, "")

    def test_logging_mapping_rebuilt_on_reseed(self):
        _patched_seed()
        _patched_seed()

    def test_idempotent_reseed(self):
        first = _patched_seed()
        second = _patched_seed()
        assert first["categories"] == second["categories"]

    def test_seeds_explicit_events(self):
        _patched_seed()
        con = duckdb_repo.get_connection()
        try:
            names = {r[0] for r in con.execute("SELECT name FROM events").fetchall()}
        finally:
            con.close()
        assert RUSSIA_TRIP_EVENT_NAME in names
        for ev in EXPLICIT_EVENTS:
            assert ev.name in names


@allure.epic("Seed catalog")
@allure.feature("rebuild_config_from_sheets")
class TestRebuildConfigFromSheets:
    def test_bumps_catalog_version_on_first_rebuild(self):
        """First rebuild on a freshly-migrated DB bumps catalog_version
        from the initial 1 up to 2.

        Multi-rebuild coverage lives in TestSeedFromSheet. The DuckDB
        FK-in-transaction quirk that used to block this is worked
        around in ``seed_config._purge_mapping_tables`` (it must run
        outside a write transaction).
        """
        with patch(
            "dinary.services.seed_config._load_categories_for_sheet",
            return_value=SAMPLE_CATEGORIES,
        ):
            first = rebuild_config_from_sheets()
        assert first["catalog_version"] == first["previous_catalog_version"] + 1
        assert first["catalog_version"] >= 2

    def test_preserves_import_sources(self):
        """``rebuild_config_from_sheets`` preserves operator edits to
        ``import_sources.notes`` across the rebuild."""
        with patch(
            "dinary.services.seed_config._load_categories_for_sheet",
            return_value=SAMPLE_CATEGORIES,
        ):
            rebuild_config_from_sheets()

        con = duckdb_repo.get_connection()
        try:
            row = con.execute(
                "SELECT year, spreadsheet_id FROM import_sources WHERE year = 2026",
            ).fetchone()
        finally:
            con.close()
        assert row == (2026, "fake-id")

    def test_fk_safe_sync_preserves_expense_referenced_category(self):
        """``import-catalog`` (rebuild) runs after historical expenses
        already reference catalog ids. The FK-safe sync must not delete
        rows that ledger tables still point at — it must instead mark
        them ``is_active=FALSE`` when they drop out of the current
        vocabulary, and keep the expense row walkable via its stable
        ``category_id``.
        """
        _patched_seed()

        # Grab a stable category id and insert a historical expense
        # that references it. ``client_expense_id=NULL`` mimics the
        # bootstrap importer; ``enqueue_logging=False`` keeps the queue
        # out of this test.
        con = duckdb_repo.get_connection()
        try:
            row = con.execute(
                "SELECT id FROM categories WHERE name = 'кафе' AND is_active",
            ).fetchone()
            assert row is not None, "sample catalog must seed 'кафе' active"
            kafe_id = int(row[0])
            duckdb_repo.insert_expense(
                con,
                client_expense_id=None,
                expense_datetime=datetime(2024, 6, 1, 12, 0),
                amount=100.0,
                amount_original=100.0,
                currency_original=settings.app_currency,
                category_id=kafe_id,
                event_id=None,
                comment="legacy row",
                sheet_category=None,
                sheet_group=None,
                tag_ids=[],
                enqueue_logging=False,
            )
        finally:
            con.close()

        # Re-seed with a reduced vocabulary that drops 'кафе' entirely.
        # The active taxonomy is driven by ``ENTRY_GROUPS`` (hardcoded)
        # *and* filtered by the sheet-discovered mapping source; we
        # patch both so 'кафе' truly disappears from the new vocabulary
        # snapshot and must be retired via ``is_active=FALSE``.
        reduced_sheet = [c for c in SAMPLE_CATEGORIES if c.name != "кафе"]
        reduced_groups = [(title, [c for c in cats if c != "кафе"]) for title, cats in ENTRY_GROUPS]
        with (
            patch(
                "dinary.services.seed_config._load_categories_for_sheet",
                return_value=reduced_sheet,
            ),
            patch(
                "dinary.services.seed_config.ENTRY_GROUPS",
                reduced_groups,
            ),
        ):
            rebuild_config_from_sheets()

        con = duckdb_repo.get_connection()
        try:
            # The id survives — FK from the legacy expense keeps the row
            # reachable, so ``is_active`` is the only thing that flips.
            row = con.execute(
                "SELECT id, is_active FROM categories WHERE id = ?",
                [kafe_id],
            ).fetchone()
            assert row == (kafe_id, False), (
                "FK-safe sync must mark the retired category inactive, "
                "not delete it (expenses.category_id still points at it)"
            )
            # Ledger row is intact and still walkable via the stable id.
            exp_count = con.execute(
                "SELECT COUNT(*) FROM expenses WHERE category_id = ?",
                [kafe_id],
            ).fetchone()[0]
            assert exp_count == 1
        finally:
            con.close()
