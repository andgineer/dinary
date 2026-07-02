import sqlite3

import allure
import pytest

from dinary.db import category_seed, storage
from dinary.db.catalog import list_visible_categories
from dinary.db.category_apply import apply_template


@pytest.fixture
def fresh_con(db):  # noqa: ARG001
    with storage.connection() as connection:
        category_seed.seed_category_templates(connection)
        yield connection


@pytest.fixture
def con(db):  # noqa: ARG001
    with storage.connection() as connection:
        category_seed.seed_category_templates(connection)
        # 'simple' makes 'groceries' visible (is_active=1) and 'fruit'
        # hidden-bucket (is_active=0); both get a group_id placement.
        apply_template(connection, "simple", "ru")
        yield connection


def _visible_codes(con: sqlite3.Connection) -> set[str]:
    return {row.code for row in list_visible_categories(con)}


@allure.epic("Category templates")
@allure.feature("Visibility")
class TestListVisibleCategories:
    def test_fresh_seed_has_no_visible_categories(self, fresh_con):
        assert list_visible_categories(fresh_con) == []

    def test_active_not_hidden_not_retired_is_visible(self, con):
        assert "groceries" in _visible_codes(con)

    def test_visible_category_carries_group_code(self, con):
        rows = {row.code: row for row in list_visible_categories(con)}

        assert rows["groceries"].group_code

    def test_inactive_is_not_visible(self, con):
        assert "fruit" not in _visible_codes(con)

    def test_hidden_overrides_active(self, con):
        con.execute("UPDATE categories SET is_hidden = 1 WHERE code = 'groceries'")

        assert "groceries" not in _visible_codes(con)

    def test_retired_overrides_active(self, con):
        con.execute("UPDATE categories SET is_retired = 1 WHERE code = 'groceries'")

        assert "groceries" not in _visible_codes(con)
