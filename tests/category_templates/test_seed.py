"""Tests for dinary.db.category_seed: idempotent fresh-seed and reconcile."""

import allure
import pytest

from dinary.category_templates import loader
from dinary.category_templates.loader import Template
from dinary.db import category_seed, storage


@pytest.fixture
def con(db):  # noqa: ARG001
    with storage.connection() as connection:
        yield connection


def _fixture(codes: list[str], *, template_code: str = "simple") -> tuple[dict, list[Template]]:
    vocabulary = {code: {"en": code, "ru": code} for code in codes}
    template = Template(
        code=template_code,
        names={"en": "Simple", "ru": "Просто"},
        taglines={"en": "Basics", "ru": "Основное"},
        groups={"grp": {"en": "Group", "ru": "Группа"}},
        renames={},
        visible={"grp": codes},
        hidden={},
    )
    return vocabulary, [template]


@allure.epic("Category templates")
@allure.feature("Seed")
class TestFreshSeed:
    def test_seeds_vocabulary_categories_groups_and_templates(self, con):
        category_seed.seed_category_templates(con)

        vocabulary = loader.load_vocabulary()
        templates = loader.load_templates()

        cat_count = con.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
        assert cat_count == len(vocabulary)

        translation_count = con.execute("SELECT COUNT(*) FROM category_translations").fetchone()[0]
        assert translation_count == sum(len(names) for names in vocabulary.values())

        rows = con.execute(
            "SELECT code, sort_order FROM category_templates ORDER BY sort_order",
        ).fetchall()
        expected = sorted(templates, key=lambda t: category_seed.TEMPLATE_SORT_ORDER[t.code])
        assert [r["code"] for r in rows] == [t.code for t in expected]
        assert [r["sort_order"] for r in rows] == [
            category_seed.TEMPLATE_SORT_ORDER[t.code] for t in expected
        ]

    def test_no_active_template_after_fresh_seed(self, con):
        category_seed.seed_category_templates(con)

        row = con.execute(
            "SELECT value FROM app_metadata WHERE key = 'active_template'",
        ).fetchone()
        assert row is None

    def test_rerun_is_idempotent(self, con):
        category_seed.seed_category_templates(con)

        def _counts() -> tuple[int, int, int, int]:
            return (
                con.execute("SELECT COUNT(*) FROM categories").fetchone()[0],
                con.execute("SELECT COUNT(*) FROM category_groups").fetchone()[0],
                con.execute("SELECT COUNT(*) FROM category_translations").fetchone()[0],
                con.execute("SELECT COUNT(*) FROM category_templates").fetchone()[0],
            )

        before = _counts()
        category_seed.seed_category_templates(con)
        assert _counts() == before


@allure.epic("Category templates")
@allure.feature("Seed")
class TestReconcile:
    def test_vanished_code_is_retired_not_deleted(self, con, monkeypatch):
        vocab1, templates1 = _fixture(["alpha", "beta"])
        monkeypatch.setattr(loader, "load_vocabulary", lambda: vocab1)
        monkeypatch.setattr(loader, "load_templates", lambda: templates1)
        category_seed.seed_category_templates(con)

        alpha_id = con.execute(
            "SELECT id FROM categories WHERE code = 'alpha'",
        ).fetchone()["id"]
        with storage.transaction(con):
            con.execute(
                "INSERT INTO expenses "
                "(client_expense_id, datetime, amount, amount_original,"
                " currency_original, category_id)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                ["exp-1", "2026-06-01 12:00:00", 100, 100, "RSD", alpha_id],
            )

        vocab2, templates2 = _fixture(["beta"])
        monkeypatch.setattr(loader, "load_vocabulary", lambda: vocab2)
        monkeypatch.setattr(loader, "load_templates", lambda: templates2)
        category_seed.seed_category_templates(con)

        alpha = con.execute(
            "SELECT code, is_active, is_retired FROM categories WHERE id = ?",
            [alpha_id],
        ).fetchone()
        assert alpha["code"] == "alpha"
        assert alpha["is_active"] == 0
        assert alpha["is_retired"] == 1

        fk_problems = con.execute("PRAGMA foreign_key_check").fetchall()
        assert fk_problems == []

    def test_user_categories_survive_reconcile(self, con, monkeypatch):
        vocab, templates = _fixture(["alpha", "beta"])
        monkeypatch.setattr(loader, "load_vocabulary", lambda: vocab)
        monkeypatch.setattr(loader, "load_templates", lambda: templates)
        category_seed.seed_category_templates(con)

        grp_id = con.execute(
            "SELECT id FROM category_groups WHERE code = 'grp'",
        ).fetchone()["id"]
        with storage.transaction(con):
            con.execute(
                "INSERT INTO categories (name, group_id, is_active, code, is_hidden, is_retired)"
                " VALUES ('Custom', ?, 1, 'u_custom', 0, 0)",
                [grp_id],
            )

        category_seed.seed_category_templates(con)

        custom = con.execute(
            "SELECT is_active, is_retired FROM categories WHERE code = 'u_custom'",
        ).fetchone()
        assert custom["is_active"] == 1
        assert custom["is_retired"] == 0
