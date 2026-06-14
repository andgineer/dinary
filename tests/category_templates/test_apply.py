"""Tests for dinary.db.category_apply.apply_template and resolve_category_name."""

import allure
import pytest

from dinary.db import category_seed, storage
from dinary.db.catalog import get_catalog_version, list_visible_categories
from dinary.db.category_apply import (
    apply_template,
    load_category_translations,
    resolve_category_name,
)


@pytest.fixture
def con(db):  # noqa: ARG001
    with storage.connection() as connection:
        category_seed.seed_category_templates(connection)
        yield connection


@allure.epic("Category templates")
@allure.feature("Apply")
class TestApplyTemplate:
    def test_unknown_template_raises(self, con):
        with pytest.raises(ValueError, match="Unknown category template"):
            apply_template(con, "does-not-exist", "ru")

    def test_records_active_template_and_bumps_catalog_version(self, con):
        version_before = get_catalog_version(con)

        apply_template(con, "simple", "ru")

        row = con.execute(
            "SELECT value FROM app_metadata WHERE key = 'active_template'",
        ).fetchone()
        assert row["value"] == "simple"
        assert get_catalog_version(con) == version_before + 1

    def test_visible_categories_get_group_and_active_flag(self, con):
        apply_template(con, "simple", "ru")

        row = con.execute(
            "SELECT c.is_active, c.name, g.code AS group_code "
            "FROM categories c JOIN category_groups g ON g.id = c.group_id "
            "WHERE c.code = 'groceries'",
        ).fetchone()
        assert row["is_active"] == 1
        assert row["group_code"] == "food"
        assert row["name"] == "продукты"

    def test_hidden_categories_are_inactive(self, con):
        apply_template(con, "simple", "ru")

        row = con.execute(
            "SELECT is_active FROM categories WHERE code = 'fruit'",
        ).fetchone()
        assert row["is_active"] == 0

    def test_group_names_and_sort_order_match_template_language(self, con):
        apply_template(con, "simple", "ru")

        rows = con.execute(
            "SELECT code, name, sort_order FROM category_groups "
            "WHERE code IN ('food', 'housing', 'life', 'growth', 'leisure') "
            "ORDER BY sort_order",
        ).fetchall()
        assert [r["code"] for r in rows] == ["food", "housing", "life", "growth", "leisure"]
        assert [r["name"] for r in rows] == ["Еда", "Жильё", "Жизнь", "Развитие", "Досуг"]

    def test_reapplying_a_different_template_moves_categories(self, con):
        apply_template(con, "simple", "ru")
        apply_template(con, "active", "ru")

        row = con.execute(
            "SELECT c.is_active, g.code AS group_code "
            "FROM categories c JOIN category_groups g ON g.id = c.group_id "
            "WHERE c.code = 'fruit'",
        ).fetchone()
        assert row["is_active"] == 1
        assert row["group_code"] == "food"

        active_template = con.execute(
            "SELECT value FROM app_metadata WHERE key = 'active_template'",
        ).fetchone()
        assert active_template["value"] == "active"

    def test_used_category_dropped_from_visible_set_stays_visible(self, con):
        """A category with expenses keeps showing up via ``list_visible_categories``
        even after a template switch moves it into the new template's hidden bucket."""
        apply_template(con, "active", "ru")  # 'fruit' is visible here

        cat_id = con.execute("SELECT id FROM categories WHERE code = 'fruit'").fetchone()[0]
        con.execute(
            "INSERT INTO expenses"
            " (datetime, amount, amount_original, currency_original, category_id)"
            " VALUES ('2026-05-01T10:00:00', 100.0, 100.0, 'EUR', ?)",
            [cat_id],
        )

        apply_template(con, "simple", "ru")  # 'fruit' is hidden here

        row = con.execute("SELECT is_active FROM categories WHERE code = 'fruit'").fetchone()
        assert row["is_active"] == 0

        codes = {row.code for row in list_visible_categories(con)}
        assert "fruit" in codes

    def test_hidden_category_stays_hidden_across_apply(self, con):
        """``apply_template`` never touches ``is_hidden`` — a user-hidden
        category remains hidden even when the new template marks it visible."""
        apply_template(con, "simple", "ru")  # 'groceries' is visible here
        con.execute("UPDATE categories SET is_hidden = 1 WHERE code = 'groceries'")

        apply_template(con, "active", "ru")  # 'groceries' is visible here too

        row = con.execute(
            "SELECT is_active, is_hidden FROM categories WHERE code = 'groceries'",
        ).fetchone()
        assert row["is_active"] == 1
        assert row["is_hidden"] == 1

        codes = {row.code for row in list_visible_categories(con)}
        assert "groceries" not in codes


@allure.epic("Category templates")
@allure.feature("Apply")
class TestResolveCategoryName:
    def test_rename_override_wins(self, con):
        definition = {"renames": {"groceries": {"ru": "Моя еда"}}}
        translations = load_category_translations(con)

        assert resolve_category_name(translations, definition, "groceries", "ru") == "Моя еда"

    def test_falls_back_to_translation(self, con):
        definition = {"renames": {}}
        translations = load_category_translations(con)

        assert resolve_category_name(translations, definition, "groceries", "ru") == "продукты"

    def test_falls_back_to_default_lang_when_requested_lang_missing(self, con):
        definition = {"renames": {}}
        translations = load_category_translations(con)

        assert resolve_category_name(translations, definition, "groceries", "fr") == "продукты"

    def test_falls_back_to_code_when_no_translation_exists(self, con):
        definition = {"renames": {}}
        translations = load_category_translations(con)

        assert (
            resolve_category_name(translations, definition, "no_such_code", "ru") == "no_such_code"
        )
