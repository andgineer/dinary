import allure
import pytest

from dinary.db import category_seed, storage
from dinary.db.catalog import (
    activate_category,
    get_catalog_version,
    hide_category,
    list_visible_categories,
    unhide_category,
)
from dinary.db.category_apply import apply_template


@pytest.fixture
def con(db):  # noqa: ARG001
    with storage.connection() as connection:
        category_seed.seed_category_templates(connection)
        apply_template(connection, "simple", "ru")
        yield connection


def _visible_codes(con):
    return {row.code for row in list_visible_categories(con)}


@allure.epic("Category templates")
@allure.feature("Activate")
class TestActivateCategory:
    def test_makes_inactive_category_visible(self, con):
        activate_category(con, "fruit")

        row = con.execute(
            "SELECT is_active, is_hidden, group_id FROM categories WHERE code = 'fruit'",
        ).fetchone()
        assert row["is_active"] == 1
        assert row["is_hidden"] == 0
        assert row["group_id"] is not None
        assert "fruit" in _visible_codes(con)

    def test_clears_hidden_flag(self, con):
        con.execute("UPDATE categories SET is_hidden = 1 WHERE code = 'fruit'")

        activate_category(con, "fruit")

        row = con.execute("SELECT is_hidden FROM categories WHERE code = 'fruit'").fetchone()
        assert row["is_hidden"] == 0

    def test_bumps_catalog_version(self, con):
        before = get_catalog_version(con)

        activate_category(con, "fruit")

        assert get_catalog_version(con) == before + 1

    def test_unknown_code_raises(self, con):
        with pytest.raises(ValueError, match="Unknown category code"):
            activate_category(con, "does_not_exist")

    def test_places_in_active_template_group_when_unplaced(self, con):
        con.execute("UPDATE categories SET group_id = NULL WHERE code = 'fruit'")

        activate_category(con, "fruit")

        row = con.execute(
            "SELECT g.code AS group_code FROM categories c"
            " JOIN category_groups g ON g.id = c.group_id"
            " WHERE c.code = 'fruit'",
        ).fetchone()
        assert row["group_code"] == "food"

    def test_unplaced_category_with_no_active_template_raises(self, db):  # noqa: ARG002
        with storage.connection() as connection:
            category_seed.seed_category_templates(connection)
            connection.execute("UPDATE categories SET group_id = NULL WHERE code = 'fruit'")

            with pytest.raises(ValueError, match="no group could be resolved"):
                activate_category(connection, "fruit")


@allure.epic("Category templates")
@allure.feature("Hide / unhide")
class TestHideUnhideCategory:
    def test_hide_removes_from_visible_set(self, con):
        # 'groceries' is visible under 'simple'.
        hide_category(con, "groceries")

        assert "groceries" not in _visible_codes(con)

    def test_hide_unknown_code_raises(self, con):
        with pytest.raises(ValueError, match="Unknown category code"):
            hide_category(con, "does_not_exist")

    def test_unhide_restores_active_category(self, con):
        hide_category(con, "groceries")

        unhide_category(con, "groceries")

        assert "groceries" in _visible_codes(con)

    def test_unhide_inactive_unused_category_stays_invisible(self, con):
        # 'fruit' is hidden-bucket (is_active=0) under 'simple'.
        hide_category(con, "fruit")

        unhide_category(con, "fruit")

        assert "fruit" not in _visible_codes(con)

    def test_unhide_unknown_code_raises(self, con):
        with pytest.raises(ValueError, match="Unknown category code"):
            unhide_category(con, "does_not_exist")
