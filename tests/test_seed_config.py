"""Tests for the config.duckdb seeding logic."""

from unittest.mock import patch

import allure

from dinary.services import duckdb_repo
from dinary.services.category_store import Category
from dinary.services.seed_config import (
    BENEFICIARY_GROUPS,
    TAG_GROUPS,
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

    def test_creates_groups_and_categories(self, monkeypatch, tmp_path):
        summary = self._seed(monkeypatch, tmp_path)
        assert summary["category_groups"] > 0
        assert summary["categories"] > 0

    def test_creates_beneficiaries(self, monkeypatch, tmp_path):
        self._seed(monkeypatch, tmp_path)
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            names = {r[0] for r in con.execute("SELECT name FROM family_members").fetchall()}
            for ben_name in BENEFICIARY_GROUPS.values():
                assert ben_name in names
        finally:
            con.close()

    def test_creates_tags(self, monkeypatch, tmp_path):
        self._seed(monkeypatch, tmp_path)
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            names = {r[0] for r in con.execute("SELECT name FROM tags").fetchall()}
            for tag_name in TAG_GROUPS.values():
                assert tag_name in names
        finally:
            con.close()

    def test_beneficiary_mapping_has_beneficiary_id(self, monkeypatch, tmp_path):
        self._seed(monkeypatch, tmp_path)
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            row = con.execute(
                "SELECT beneficiary_id FROM sheet_category_mapping "
                "WHERE sheet_category = 'еда&бытовые' AND sheet_group = 'собака'"
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
                "SELECT tag_ids FROM sheet_category_mapping "
                "WHERE sheet_category = 'обустройство' AND sheet_group = 'релокация'"
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
                "SELECT event_id FROM sheet_category_mapping "
                "WHERE sheet_category = 'топливо' AND sheet_group = 'путешествия'"
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

    def test_no_group_category_maps_to_empty_group(self, monkeypatch, tmp_path):
        self._seed(monkeypatch, tmp_path)
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            row = con.execute(
                "SELECT category_id FROM sheet_category_mapping "
                "WHERE sheet_category = 'мобильник' AND sheet_group = ''"
            ).fetchone()
            assert row is not None
        finally:
            con.close()

    def test_приложения_is_regular_group_not_tag(self, monkeypatch, tmp_path):
        """'приложения' should map as a regular category group, not produce a tag."""
        self._seed(monkeypatch, tmp_path)
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            row = con.execute(
                "SELECT tag_ids FROM sheet_category_mapping "
                "WHERE sheet_category = 'развлечения' AND sheet_group = 'приложения'"
            ).fetchone()
            assert row is not None
            assert row[0] is None
        finally:
            con.close()

    def test_idempotent(self, monkeypatch, tmp_path):
        """Running seed twice produces the same result."""
        s1 = self._seed(monkeypatch, tmp_path)
        s2 = self._seed(monkeypatch, tmp_path)
        assert s1["category_groups"] == s2["category_groups"]
        assert s1["categories"] == s2["categories"]
        assert s2["mappings_created"] == 0

    def test_all_sample_categories_mapped(self, monkeypatch, tmp_path):
        self._seed(monkeypatch, tmp_path)
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            count = con.execute("SELECT COUNT(*) FROM sheet_category_mapping").fetchone()[0]
            assert count == len(SAMPLE_CATEGORIES)
        finally:
            con.close()
