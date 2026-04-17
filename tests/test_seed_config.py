"""Tests for the config.duckdb seeding logic."""

from unittest.mock import patch

import allure

from dinary.config import settings
from dinary.services import duckdb_repo
from dinary.services.category_store import Category
from dinary.services.seed_config import (
    BENEFICIARY_ENVELOPES,
    ENTRY_GROUPS,
    SPHERE_OF_LIFE_ENVELOPES,
    rebuild_config_from_sheets,
    rebuild_taxonomy,
    seed_from_sheet,
)

SAMPLE_CATEGORIES = [
    Category(name="еда&бытовые", group="собака"),
    Category(name="карманные", group="ребенок"),
    Category(name="булавки", group="лариса"),
    Category(name="обустройство", group="релокация"),
    Category(name="обучение", group="профессиональное"),
    Category(name="топливо", group="путешествия"),
    Category(name="кафе", group="путешествия"),
    Category(name="мобильник", group=""),
    Category(name="еда&бытовые", group=""),
    Category(name="интернет", group=""),
    Category(name="развлечения", group=""),
]


@allure.epic("DuckDB")
@allure.feature("Seed Config")
class TestSeedConfig:
    def _seed(self, monkeypatch, tmp_path, year=2026):
        monkeypatch.setattr(duckdb_repo, "DATA_DIR", tmp_path)
        monkeypatch.setattr(duckdb_repo, "CONFIG_DB", tmp_path / "config.duckdb")
        with patch(
            "dinary.services.seed_config.get_categories",
            return_value=SAMPLE_CATEGORIES,
        ):
            return seed_from_sheet(year=year)

    def test_creates_categories(self, monkeypatch, tmp_path):
        summary = self._seed(monkeypatch, tmp_path)
        assert summary["categories"] > 0

    def test_creates_beneficiaries(self, monkeypatch, tmp_path):
        self._seed(monkeypatch, tmp_path)
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            names = {r[0] for r in con.execute("SELECT name FROM family_members").fetchall()}
            for ben_name in BENEFICIARY_ENVELOPES.values():
                assert ben_name in names
        finally:
            con.close()

    def test_creates_spheres_of_life(self, monkeypatch, tmp_path):
        self._seed(monkeypatch, tmp_path)
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            names = {r[0] for r in con.execute("SELECT name FROM spheres_of_life").fetchall()}
            for sphere_name in SPHERE_OF_LIFE_ENVELOPES.values():
                assert sphere_name in names
        finally:
            con.close()

    def test_beneficiary_mapping_has_beneficiary_id(self, monkeypatch, tmp_path):
        self._seed(monkeypatch, tmp_path)
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            row = con.execute(
                "SELECT beneficiary_id FROM source_type_mapping "
                "WHERE source_type = 'еда&бытовые' AND source_envelope = 'собака'"
            ).fetchone()
            assert row is not None
            assert row[0] is not None
        finally:
            con.close()

    def test_sphere_mapping_has_sphere_id(self, monkeypatch, tmp_path):
        self._seed(monkeypatch, tmp_path)
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            row = con.execute(
                "SELECT sphere_of_life_id FROM source_type_mapping "
                "WHERE source_type = 'обустройство' AND source_envelope = 'релокация'"
            ).fetchone()
            assert row is not None
            assert row[0] is not None
        finally:
            con.close()

    def test_travel_mapping_has_null_event_id(self, monkeypatch, tmp_path):
        self._seed(monkeypatch, tmp_path)
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            row = con.execute(
                "SELECT event_id FROM source_type_mapping "
                "WHERE source_type = 'топливо' AND source_envelope = 'путешествия'"
            ).fetchone()
            assert row is not None
            assert row[0] is None
        finally:
            con.close()

    def test_creates_synthetic_travel_event(self, monkeypatch, tmp_path):
        self._seed(monkeypatch, tmp_path)
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            row = con.execute("SELECT name FROM events WHERE name = 'отпуск-2026'").fetchone()
            assert row is not None
        finally:
            con.close()

    def test_no_group_category_maps_to_empty_envelope(self, monkeypatch, tmp_path):
        self._seed(monkeypatch, tmp_path)
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            row = con.execute(
                "SELECT category_id FROM source_type_mapping "
                "WHERE source_type = 'мобильник' AND source_envelope = ''"
            ).fetchone()
            assert row is not None
        finally:
            con.close()

    def test_булавки_maps_to_карманные_with_лариса(self, monkeypatch, tmp_path):
        """'булавки' should map to category 'карманные' with beneficiary 'Лариса'."""
        self._seed(monkeypatch, tmp_path)
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            row = con.execute(
                "SELECT category_id, beneficiary_id FROM source_type_mapping "
                "WHERE source_type = 'булавки' AND source_envelope = 'лариса'"
            ).fetchone()
            assert row is not None
            cat_name = con.execute("SELECT name FROM categories WHERE id = ?", [row[0]]).fetchone()[
                0
            ]
            assert cat_name == "карманные"
            ben_name = con.execute(
                "SELECT name FROM family_members WHERE id = ?", [row[1]]
            ).fetchone()[0]
            assert ben_name == "Лариса"
        finally:
            con.close()

    def test_idempotent(self, monkeypatch, tmp_path):
        s1 = self._seed(monkeypatch, tmp_path)
        s2 = self._seed(monkeypatch, tmp_path)
        assert s1["categories"] == s2["categories"]
        assert s2["mappings_created"] == 0

    def test_all_sample_categories_mapped(self, monkeypatch, tmp_path):
        self._seed(monkeypatch, tmp_path)
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            count = con.execute("SELECT COUNT(*) FROM source_type_mapping").fetchone()[0]
            assert count >= len(SAMPLE_CATEGORIES)
        finally:
            con.close()

    def test_seeds_explicit_historical_mapping_overrides(self, monkeypatch, tmp_path):
        """Historical mappings should be inserted even if absent from current sheet."""
        self._seed(monkeypatch, tmp_path)
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            row = con.execute(
                "SELECT year, category_id, sphere_of_life_id FROM source_type_mapping "
                "WHERE source_type = 'professional' AND source_envelope = 'apps'"
            ).fetchone()
            assert row is not None
            assert row[0] == 2023
            cat_name = con.execute("SELECT name FROM categories WHERE id = ?", [row[1]]).fetchone()[
                0
            ]
            assert cat_name == "продуктивность"
            sphere_name = con.execute(
                "SELECT name FROM spheres_of_life WHERE id = ?", [row[2]]
            ).fetchone()[0]
            assert sphere_name == "профессиональное"
        finally:
            con.close()

    def test_legacy_food_maps_to_еда(self, monkeypatch, tmp_path):
        self._seed(monkeypatch, tmp_path)
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            row = con.execute(
                "SELECT category_id FROM source_type_mapping "
                "WHERE source_type = 'еда&бытовые' AND source_envelope = ''"
            ).fetchone()
            assert row is not None
            cat_name = con.execute("SELECT name FROM categories WHERE id = ?", [row[0]]).fetchone()[
                0
            ]
            assert cat_name == "еда"
        finally:
            con.close()

    def test_categories_are_atomic(self, monkeypatch, tmp_path):
        self._seed(monkeypatch, tmp_path)
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            cols = [
                r[0]
                for r in con.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'categories'"
                ).fetchall()
            ]
            assert "group_id" not in cols
            assert "id" in cols
            assert "name" in cols
        finally:
            con.close()

    def test_taxonomy_rebuild(self, monkeypatch, tmp_path):
        self._seed(monkeypatch, tmp_path)
        con = duckdb_repo.get_config_connection(read_only=False)
        try:
            memberships = rebuild_taxonomy(con)
            assert memberships > 0

            taxonomy = con.execute(
                "SELECT key, title FROM category_taxonomies WHERE id = 1"
            ).fetchone()
            assert taxonomy == ("entry_groups", "Entry Groups")

            nodes = con.execute("SELECT COUNT(*) FROM category_taxonomy_nodes").fetchone()[0]
            assert nodes == len(ENTRY_GROUPS)

            mem_count = con.execute("SELECT COUNT(*) FROM category_taxonomy_membership").fetchone()[
                0
            ]
            assert mem_count == memberships
        finally:
            con.close()

    def test_taxonomy_rebuild_idempotent(self, monkeypatch, tmp_path):
        self._seed(monkeypatch, tmp_path)
        con = duckdb_repo.get_config_connection(read_only=False)
        try:
            m1 = rebuild_taxonomy(con)
            m2 = rebuild_taxonomy(con)
            assert m1 == m2
        finally:
            con.close()

    def test_travel_does_not_create_category_group(self, monkeypatch, tmp_path):
        self._seed(monkeypatch, tmp_path)
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            row = con.execute("SELECT 1 FROM categories WHERE name = 'путешествия'").fetchone()
            assert row is None
        finally:
            con.close()

    def test_ensures_all_taxonomy_categories_exist(self, monkeypatch, tmp_path):
        """All categories listed in ENTRY_GROUPS should exist after seeding."""
        self._seed(monkeypatch, tmp_path)
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            for _group_title, category_names in ENTRY_GROUPS:
                for cat_name in category_names:
                    row = con.execute(
                        "SELECT 1 FROM categories WHERE name = ?", [cat_name]
                    ).fetchone()
                    assert row is not None, f"Category '{cat_name}' not found"
        finally:
            con.close()

    def test_rebuild_preserves_import_sources_and_loads_categories_from_them(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(duckdb_repo, "DATA_DIR", tmp_path)
        monkeypatch.setattr(duckdb_repo, "CONFIG_DB", tmp_path / "config.duckdb")

        duckdb_repo.init_config_db()
        con = duckdb_repo.get_config_connection(read_only=False)
        try:
            con.execute(
                "INSERT INTO sheet_import_sources VALUES (2023, 'hist-sheet', 'hist', 'default', NULL)"
            )
        finally:
            con.close()

        hist_ws = type(
            "WS",
            (),
            {
                "get_all_values": lambda self: [
                    ["Date", "RSD", "EUR", "Category", "Group", "Comment", "Month", "Rate"],
                    ["2023-01-01", "0", "", "professional", "apps", "", "1", ""],
                ]
            },
        )()
        hist_ss = type("SS", (), {"worksheet": lambda self, name: hist_ws, "sheet1": hist_ws})()

        with (
            patch("dinary.services.seed_config.get_categories", return_value=SAMPLE_CATEGORIES),
            patch("dinary.services.seed_config.get_sheet", return_value=hist_ss),
        ):
            summary = rebuild_config_from_sheets()

        assert summary["preserved_import_sources"] == 1
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            source_row = con.execute(
                "SELECT spreadsheet_id, worksheet_name, layout_key FROM sheet_import_sources WHERE year = 2023"
            ).fetchone()
            assert source_row == ("hist-sheet", "hist", "default")

            mapping_row = con.execute(
                "SELECT year FROM source_type_mapping WHERE source_type = 'professional' AND source_envelope = 'apps'"
            ).fetchone()
            assert mapping_row is not None
        finally:
            con.close()

    def test_collects_categories_from_default_spreadsheet_import_source(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(duckdb_repo, "DATA_DIR", tmp_path)
        monkeypatch.setattr(duckdb_repo, "CONFIG_DB", tmp_path / "config.duckdb")

        duckdb_repo.init_config_db()
        con = duckdb_repo.get_config_connection(read_only=False)
        try:
            con.execute(
                "INSERT INTO sheet_import_sources VALUES (2026, '', 'hist', 'default', NULL)"
            )
        finally:
            con.close()

        hist_ws = type(
            "WS",
            (),
            {
                "get_all_values": lambda self: [
                    ["Date", "RSD", "EUR", "Category", "Group", "Comment", "Month", "Rate"],
                    ["2026-01-01", "0", "", "еда", "special-group", "", "1", ""],
                ]
            },
        )()
        hist_ss = type("SS", (), {"worksheet": lambda self, name: hist_ws, "sheet1": hist_ws})()

        with (
            patch("dinary.services.seed_config.get_categories", return_value=SAMPLE_CATEGORIES),
            patch("dinary.services.seed_config.get_sheet", return_value=hist_ss) as mock_get_sheet,
        ):
            seed_from_sheet()

        mock_get_sheet.assert_called_with("")
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            mapping_row = con.execute(
                "SELECT 1 FROM source_type_mapping WHERE source_type = 'еда' AND source_envelope = 'special-group'"
            ).fetchone()
            assert mapping_row is not None
        finally:
            con.close()

    def test_rebuild_bootstraps_import_sources_from_env_json(self, monkeypatch, tmp_path):
        monkeypatch.setattr(duckdb_repo, "DATA_DIR", tmp_path)
        monkeypatch.setattr(duckdb_repo, "CONFIG_DB", tmp_path / "config.duckdb")
        monkeypatch.setattr(
            settings,
            "sheet_import_sources_json",
            '[{"year": 2026, "spreadsheet_id": "boot-sheet", "worksheet_name": "", "layout_key": "eur_primary"}]',
        )

        boot_ws = type(
            "WS",
            (),
            {
                "get_all_values": lambda self: [
                    ["Date", "RSD", "EUR", "Category", "Group", "Comment", "Month", "Rate"],
                    ["2026-01-01", "0", "", "еда", "", "", "1", ""],
                ]
            },
        )()
        boot_ss = type("SS", (), {"worksheet": lambda self, name: boot_ws, "sheet1": boot_ws})()

        with (
            patch("dinary.services.seed_config.get_categories", return_value=SAMPLE_CATEGORIES),
            patch("dinary.services.seed_config.get_sheet", return_value=boot_ss),
        ):
            summary = rebuild_config_from_sheets()

        assert summary["preserved_import_sources"] == 0
        assert summary["bootstrapped_import_sources"] == 1

        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            source_row = con.execute(
                "SELECT spreadsheet_id, worksheet_name, layout_key FROM sheet_import_sources WHERE year = 2026"
            ).fetchone()
            assert source_row == ("boot-sheet", "", "eur_primary")
        finally:
            con.close()
