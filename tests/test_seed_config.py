"""Tests for the config.duckdb seeding logic."""

from unittest.mock import patch

import allure

from dinary.services import duckdb_repo
from dinary.services.category_store import Category
from dinary.services.seed_config import (
    BENEFICIARY_ENVELOPES,
    ENTRY_GROUPS,
    TAG_ENVELOPES,
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
    Category(name="развлечения", group="приложения"),
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

    def test_creates_tags(self, monkeypatch, tmp_path):
        self._seed(monkeypatch, tmp_path)
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            names = {r[0] for r in con.execute("SELECT name FROM tags").fetchall()}
            for tag_name in TAG_ENVELOPES.values():
                assert tag_name in names
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

    def test_tag_mapping_has_tag_ids(self, monkeypatch, tmp_path):
        self._seed(monkeypatch, tmp_path)
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            row = con.execute(
                "SELECT tag_ids FROM source_type_mapping "
                "WHERE source_type = 'обустройство' AND source_envelope = 'релокация'"
            ).fetchone()
            assert row is not None
            assert row[0] is not None
            assert len(row[0]) == 1
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

    def test_приложения_maps_with_подписка_tag(self, monkeypatch, tmp_path):
        """'приложения' envelope should produce a подписка tag."""
        self._seed(monkeypatch, tmp_path)
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            row = con.execute(
                "SELECT tag_ids FROM source_type_mapping "
                "WHERE source_type = 'развлечения' AND source_envelope = 'приложения'"
            ).fetchone()
            assert row is not None
            assert row[0] is not None
            tag_names = [
                con.execute("SELECT name FROM tags WHERE id = ?", [tid]).fetchone()[0]
                for tid in row[0]
            ]
            assert "подписка" in tag_names
        finally:
            con.close()

    def test_idempotent(self, monkeypatch, tmp_path):
        """Running seed twice produces the same result."""
        s1 = self._seed(monkeypatch, tmp_path)
        s2 = self._seed(monkeypatch, tmp_path)
        assert s1["categories"] == s2["categories"]
        assert s2["mappings_created"] == 0

    def test_all_sample_categories_mapped(self, monkeypatch, tmp_path):
        self._seed(monkeypatch, tmp_path)
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            count = con.execute("SELECT COUNT(*) FROM source_type_mapping").fetchone()[0]
            assert count == len(SAMPLE_CATEGORIES)
        finally:
            con.close()

    def test_legacy_food_maps_to_еда(self, monkeypatch, tmp_path):
        """Legacy еда&бытовые source_type should map to atomic category еда."""
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
        """Categories should have no group_id column."""
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

    def test_no_stores_table(self, monkeypatch, tmp_path):
        self._seed(monkeypatch, tmp_path)
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
            assert "stores" not in tables
        finally:
            con.close()

    def test_no_category_groups_table(self, monkeypatch, tmp_path):
        self._seed(monkeypatch, tmp_path)
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
            assert "category_groups" not in tables
        finally:
            con.close()

    def test_taxonomy_rebuild(self, monkeypatch, tmp_path):
        """rebuild_taxonomy creates entry_groups nodes and memberships."""
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
        """Running rebuild_taxonomy twice produces the same result."""
        self._seed(monkeypatch, tmp_path)
        con = duckdb_repo.get_config_connection(read_only=False)
        try:
            m1 = rebuild_taxonomy(con)
            m2 = rebuild_taxonomy(con)
            assert m1 == m2
        finally:
            con.close()

    def test_travel_does_not_create_category_group(self, monkeypatch, tmp_path):
        """Travel envelope should not create a 'путешествия' category — only uses event."""
        self._seed(monkeypatch, tmp_path)
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            row = con.execute("SELECT 1 FROM categories WHERE name = 'путешествия'").fetchone()
            assert row is None
        finally:
            con.close()
