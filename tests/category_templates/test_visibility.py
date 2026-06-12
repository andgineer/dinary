"""Tests for the visibility predicate: db.catalog.list_visible_categories.

Truth table for ``(is_active OR used) AND NOT is_hidden AND NOT is_retired``.
"""

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


def _insert_expense(con: sqlite3.Connection, category_id: int) -> None:
    con.execute(
        "INSERT INTO expenses"
        " (datetime, amount, amount_original, currency_original, category_id)"
        " VALUES ('2026-05-01T10:00:00', 100.0, 100.0, 'EUR', ?)",
        [category_id],
    )


def _category_id(con: sqlite3.Connection, code: str) -> int:
    return con.execute("SELECT id FROM categories WHERE code = ?", [code]).fetchone()[0]


@allure.epic("Category templates")
@allure.feature("Visibility")
class TestListVisibleCategories:
    def test_fresh_seed_has_no_visible_categories(self, fresh_con):
        """Before any ``apply_template``, every factory category is
        ``is_active=0``, ``group_id=NULL`` and unused — none are visible."""
        assert list_visible_categories(fresh_con) == []

    def test_active_not_hidden_not_retired_is_visible(self, con):
        assert "groceries" in _visible_codes(con)

    def test_visible_category_carries_group_code(self, con):
        rows = {row.code: row for row in list_visible_categories(con)}

        assert rows["groceries"].group_code

    def test_inactive_unused_is_not_visible(self, con):
        assert "fruit" not in _visible_codes(con)

    def test_inactive_but_used_is_visible(self, con):
        _insert_expense(con, _category_id(con, "fruit"))

        assert "fruit" in _visible_codes(con)

    def test_hidden_overrides_active(self, con):
        con.execute("UPDATE categories SET is_hidden = 1 WHERE code = 'groceries'")

        assert "groceries" not in _visible_codes(con)

    def test_hidden_overrides_used(self, con):
        con.execute("UPDATE categories SET is_hidden = 1 WHERE code = 'fruit'")
        _insert_expense(con, _category_id(con, "fruit"))

        assert "fruit" not in _visible_codes(con)

    def test_retired_overrides_active(self, con):
        con.execute("UPDATE categories SET is_retired = 1 WHERE code = 'groceries'")

        assert "groceries" not in _visible_codes(con)

    def test_retired_overrides_used(self, con):
        con.execute("UPDATE categories SET is_retired = 1 WHERE code = 'fruit'")
        _insert_expense(con, _category_id(con, "fruit"))

        assert "fruit" not in _visible_codes(con)
