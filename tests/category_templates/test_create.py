"""Tests for db.catalog.create_category, move_category, rename_category."""

import allure
import pytest

from dinary.db import category_seed, storage
from dinary.db.catalog import (
    create_category,
    list_visible_categories,
    move_category,
    rename_category,
)
from dinary.db.category_apply import apply_template


@pytest.fixture
def con(db):  # noqa: ARG001
    with storage.connection() as connection:
        category_seed.seed_category_templates(connection)
        apply_template(connection, "simple", "ru")
        yield connection


@allure.epic("Category templates")
@allure.feature("Create")
class TestCreateCategory:
    def test_slugifies_name_into_u_prefixed_code(self, con):
        code = create_category(con, "My Category!!", "food")

        assert code == "u_my_category"

    def test_non_latin_name_falls_back_to_generic_code(self, con):
        """A name with no ASCII alphanumerics slugifies to a generic placeholder."""
        code = create_category(con, "Моя категория", "food")

        assert code == "u_category"

    def test_new_category_is_immediately_visible(self, con):
        code = create_category(con, "My Thing", "food")

        codes = {row.code for row in list_visible_categories(con)}
        assert code in codes

    def test_new_category_placed_in_resolved_group(self, con):
        code = create_category(con, "My Thing", "food")

        row = con.execute(
            "SELECT g.code AS group_code FROM categories c"
            " JOIN category_groups g ON g.id = c.group_id"
            " WHERE c.code = ?",
            [code],
        ).fetchone()
        assert row["group_code"] == "food"

    def test_name_collision_gets_numeric_suffix(self, con):
        first = create_category(con, "My Thing", "food")
        second = create_category(con, "My Thing", "food")

        assert first == "u_my_thing"
        assert second == "u_my_thing_2"

    def test_unknown_group_code_raises(self, con):
        with pytest.raises(ValueError, match="Unknown category group code"):
            create_category(con, "My Thing", "does_not_exist")

    def test_survives_seed_reconcile(self, con):
        """``u_``-prefixed codes are never touched by ``seed_category_templates``."""
        code = create_category(con, "My Thing", "food")

        category_seed.seed_category_templates(con)

        row = con.execute(
            "SELECT is_active, is_retired FROM categories WHERE code = ?",
            [code],
        ).fetchone()
        assert row is not None
        assert row["is_active"] == 1
        assert row["is_retired"] == 0


@allure.epic("Category templates")
@allure.feature("Move")
class TestMoveCategory:
    def test_moves_to_new_group(self, con):
        move_category(con, "groceries", "housing")

        row = con.execute(
            "SELECT g.code AS group_code FROM categories c"
            " JOIN category_groups g ON g.id = c.group_id"
            " WHERE c.code = 'groceries'",
        ).fetchone()
        assert row["group_code"] == "housing"

    def test_unknown_category_code_raises(self, con):
        with pytest.raises(ValueError, match="Unknown category code"):
            move_category(con, "does_not_exist", "housing")

    def test_unknown_group_code_raises(self, con):
        with pytest.raises(ValueError, match="Unknown category group code"):
            move_category(con, "groceries", "does_not_exist")


@allure.epic("Category templates")
@allure.feature("Rename")
class TestRenameCategory:
    def test_changes_name_keeps_code(self, con):
        rename_category(con, "groceries", "Продукты питания")

        row = con.execute("SELECT name, code FROM categories WHERE code = 'groceries'").fetchone()
        assert row["name"] == "Продукты питания"
        assert row["code"] == "groceries"

    def test_unknown_code_raises(self, con):
        with pytest.raises(ValueError, match="Unknown category code"):
            rename_category(con, "does_not_exist", "x")
