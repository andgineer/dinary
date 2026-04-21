"""Tests for the 3D-catalog seeding logic on the unified dinary.duckdb.

These exercise both halves of the post-split pipeline:

* ``services.seed_config.bootstrap_catalog`` — the no-Sheets path that
  populates the runtime taxonomy only.
* ``imports.seed.seed_from_sheet`` / ``imports.seed.rebuild_config_from_sheets``
  — the Google-Sheets-driven path that additionally populates
  ``import_mapping`` from discovered pairs.
"""

from datetime import datetime
from unittest.mock import patch

import allure
import pytest

from dinary import config
from dinary.config import ImportSourceRow, settings
from dinary.imports.seed import (
    Category,
    rebuild_config_from_sheets,
    seed_from_sheet,
)
from dinary.services import duckdb_repo
from dinary.services.seed_config import (
    ENTRY_GROUPS,
    EXPLICIT_EVENTS,
    PHASE1_TAGS,
    RUSSIA_TRIP_EVENT_NAME,
    SYNTHETIC_EVENT_PREFIX,
    bootstrap_catalog,
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

SAMPLE_IMPORT_SOURCES = [
    ImportSourceRow(
        year=2026,
        spreadsheet_id="fake-id",
        worksheet_name="Sheet1",
        layout_key="default",
    ),
]


@pytest.fixture(autouse=True)
def _tmp_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(duckdb_repo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(duckdb_repo, "DB_PATH", tmp_path / "dinary.duckdb")


@pytest.fixture(autouse=True)
def _stub_settings(monkeypatch):
    """Provide a single import source row without touching the real JSON file.

    Patches ``dinary.config.read_import_sources`` in every module that
    imports it by name (``imports.seed`` and ``services.seed_config``
    both re-import from ``dinary.config``, so a single patch on the
    original module flows through to both callers).
    """
    monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "")
    monkeypatch.setattr(
        config,
        "read_import_sources",
        lambda: list(SAMPLE_IMPORT_SOURCES),
    )


def _patched_seed(year=2026):
    with patch(
        "dinary.imports.seed._load_categories_for_sheet",
        return_value=SAMPLE_CATEGORIES,
    ):
        return seed_from_sheet(year=year)


@allure.epic("Seed catalog")
@allure.feature("bootstrap_catalog (runtime taxonomy only)")
class TestBootstrapCatalog:
    def test_bootstrap_creates_hardcoded_taxonomy_without_sheets(self):
        """``bootstrap_catalog`` populates groups/cats/tags/events from hardcoded
        constants — no Google Sheets round-trip, no ``import_mapping`` rows."""
        summary = bootstrap_catalog()
        assert summary["category_groups"] >= len(ENTRY_GROUPS)
        assert summary["categories"] >= 1
        assert summary["tags"] == len(PHASE1_TAGS)
        assert summary["events"] >= 1

        con = duckdb_repo.get_connection()
        try:
            mapping_count = con.execute("SELECT COUNT(*) FROM import_mapping").fetchone()[0]
        finally:
            con.close()
        assert mapping_count == 0, (
            "bootstrap_catalog must NOT populate import_mapping — that's "
            "imports.seed's job and non-import users leave it empty"
        )

    def test_bootstrap_is_idempotent(self):
        first = bootstrap_catalog()
        second = bootstrap_catalog()
        assert first == second


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

    def test_seed_does_not_populate_sheet_mapping(self):
        """Runtime 3D->2D routing moved out of seed in Phase 2. Seed now
        leaves ``sheet_mapping`` empty; it's owned by
        ``sheet_mapping.py`` and populated from the hand-curated ``map``
        worksheet tab."""
        _patched_seed()

        con = duckdb_repo.get_connection()
        try:
            runtime_count = con.execute(
                "SELECT COUNT(*) FROM sheet_mapping",
            ).fetchone()[0]
            runtime_tags_count = con.execute(
                "SELECT COUNT(*) FROM sheet_mapping_tags",
            ).fetchone()[0]
        finally:
            con.close()
        assert runtime_count == 0
        assert runtime_tags_count == 0

    def test_reseed_is_idempotent(self):
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
        around in ``imports.seed._purge_mapping_tables`` (it must run
        outside a write transaction).
        """
        with patch(
            "dinary.imports.seed._load_categories_for_sheet",
            return_value=SAMPLE_CATEGORIES,
        ):
            first = rebuild_config_from_sheets()
        assert first["catalog_version"] == first["previous_catalog_version"] + 1
        assert first["catalog_version"] >= 2
        assert first["catalog_version_changed"] is True

    def test_idempotent_rebuild_does_not_bump_catalog_version(self):
        """Second rebuild with the same inputs is a no-op — the catalog
        hash doesn't change, so catalog_version stays put. Without this
        guarantee every ``inv import-catalog`` would force the PWA to
        redownload the full catalog on the next fetch for no reason.
        """
        with patch(
            "dinary.imports.seed._load_categories_for_sheet",
            return_value=SAMPLE_CATEGORIES,
        ):
            first = rebuild_config_from_sheets()
            second = rebuild_config_from_sheets()
        assert second["catalog_version"] == first["catalog_version"]
        assert second["catalog_version_changed"] is False

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
                "dinary.imports.seed._load_categories_for_sheet",
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
