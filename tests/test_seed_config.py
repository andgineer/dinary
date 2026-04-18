"""Tests for the 3D-model config.duckdb seeding logic."""

import json
from unittest.mock import patch

import allure
import pytest

from dinary.config import settings
from dinary.services import duckdb_repo
from dinary.services.category_store import Category
from dinary.services.seed_config import (
    ENTRY_GROUPS,
    EXPLICIT_EVENTS,
    PHASE1_TAGS,
    RUSSIA_TRIP_EVENT_NAME,
    SYNTHETIC_EVENT_PREFIX,
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
    monkeypatch.setattr(duckdb_repo, "CONFIG_DB", tmp_path / "config.duckdb")


@pytest.fixture(autouse=True)
def _stub_settings(monkeypatch):
    """Bootstrap a single import source so seed_from_sheet has something to read."""
    monkeypatch.setattr(settings, "google_sheets_spreadsheet_id", "")
    monkeypatch.setattr(
        settings,
        "sheet_import_sources_json",
        json.dumps(
            [
                {
                    "year": 2026,
                    "spreadsheet_id": "fake-id",
                    "worksheet_name": "Sheet1",
                    "layout_key": "default",
                },
            ]
        ),
    )


def _patched_seed(year=2026):
    # `seed_from_sheet` only goes through `_load_categories_for_sheet` now
    # (the legacy default-spreadsheet `get_categories()` call was removed),
    # so the test fixture feeds SAMPLE_CATEGORIES through that single hook.
    with patch(
        "dinary.services.seed_config._load_categories_for_sheet",
        return_value=SAMPLE_CATEGORIES,
    ):
        return seed_from_sheet(year=year)


@allure.epic("DuckDB")
@allure.feature("Seed Config (3D)")
class TestSeedFromSheet:
    def test_creates_groups(self):
        _patched_seed()
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            names = {r[0] for r in con.execute("SELECT name FROM category_groups").fetchall()}
            for group_title, _cats in ENTRY_GROUPS:
                assert group_title in names
        finally:
            con.close()

    def test_creates_categories_with_group_links(self):
        _patched_seed()
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            rows = con.execute(
                "SELECT c.name, g.name FROM categories c"
                " JOIN category_groups g ON g.id = c.group_id",
            ).fetchall()
            assert len(rows) > 0
            for cat_name, group_name in rows:
                assert cat_name and group_name
        finally:
            con.close()

    def test_creates_phase1_tags(self):
        _patched_seed()
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            names = {r[0] for r in con.execute("SELECT name FROM tags").fetchall()}
            for tag in PHASE1_TAGS:
                assert tag in names
        finally:
            con.close()

    def test_creates_per_year_synthetic_events(self):
        _patched_seed()
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            names = {r[0] for r in con.execute("SELECT name FROM events").fetchall()}
            for y in (2018, 2022, 2026):
                assert f"{SYNTHETIC_EVENT_PREFIX}{y}" in names
            assert "релокация-в-Сербию" in names
        finally:
            con.close()

    def test_creates_sheet_mappings(self):
        _patched_seed()
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            count = con.execute("SELECT COUNT(*) FROM sheet_mapping").fetchone()[0]
            assert count > 0
        finally:
            con.close()

    def test_idempotent_reseed(self):
        first = _patched_seed()
        second = _patched_seed()
        assert first["categories"] == second["categories"]

    def test_seeds_explicit_events(self):
        # Bug regression: russia-trip event was being created lazily in
        # `import_sheet` without a `catalog_version` bump. It now lives in
        # `EXPLICIT_EVENTS` and must show up after a single seed pass.
        _patched_seed()
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            names = {r[0] for r in con.execute("SELECT name FROM events").fetchall()}
            assert RUSSIA_TRIP_EVENT_NAME in names
            for ev in EXPLICIT_EVENTS:
                assert ev.name in names
        finally:
            con.close()


@allure.epic("DuckDB")
@allure.feature("rebuild_config_from_sheets")
class TestRebuildConfigFromSheets:
    def test_bumps_catalog_version(self):
        with patch(
            "dinary.services.seed_config._load_categories_for_sheet",
            return_value=SAMPLE_CATEGORIES,
        ):
            first = rebuild_config_from_sheets()
        assert first["catalog_version"] == first["previous_catalog_version"] + 1
        assert first["catalog_version"] >= 2  # 1 (initial) -> 2 after first rebuild

        with patch(
            "dinary.services.seed_config._load_categories_for_sheet",
            return_value=SAMPLE_CATEGORIES,
        ):
            second = rebuild_config_from_sheets()
        assert second["catalog_version"] == first["catalog_version"] + 1
