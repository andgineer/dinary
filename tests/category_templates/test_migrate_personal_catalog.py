"""Tests for dinary.db.category_seed.migrate_personal_catalog and bootstrap_categories."""

import allure
import pytest

from dinary.db import category_seed, storage


@pytest.fixture
def personal_con(db):  # noqa: ARG001
    """A pre-migration personal DB: real category/group names, no codes yet."""
    with storage.connection() as con:
        with storage.transaction(con):
            for i, name in enumerate(category_seed.GROUP_MAP, start=1):
                con.execute(
                    "INSERT INTO category_groups (id, name, sort_order) VALUES (?, ?, ?)",
                    [i, name, i],
                )
            group_ids = list(range(1, len(category_seed.GROUP_MAP) + 1))
            for i, name in enumerate(category_seed.CATEGORY_MAP, start=1):
                con.execute(
                    "INSERT INTO categories (name, group_id, is_active) VALUES (?, ?, 1)",
                    [name, group_ids[i % len(group_ids)]],
                )
        yield con


def _snapshot(con) -> tuple:
    return (
        con.execute("SELECT id, name, group_id, code FROM categories ORDER BY id").fetchall(),
        con.execute(
            "SELECT id, name, sort_order, code FROM category_groups ORDER BY id"
        ).fetchall(),
        con.execute("SELECT key, value FROM app_metadata ORDER BY key").fetchall(),
    )


@allure.epic("Category templates")
@allure.feature("Personal catalog migration")
class TestMigratePersonalCatalog:
    def test_backfills_category_codes(self, personal_con):
        # apply_template (run as the last migration step) rewrites categories.name,
        # so capture the pre-migration name keyed by id before that happens.
        before = {
            r["id"]: r["name"]
            for r in personal_con.execute("SELECT id, name FROM categories").fetchall()
        }

        category_seed.migrate_personal_catalog(personal_con)

        after = {
            r["id"]: r["code"]
            for r in personal_con.execute("SELECT id, code FROM categories").fetchall()
        }
        for cat_id, name in before.items():
            assert after[cat_id] == category_seed.CATEGORY_MAP[name]

    def test_backfills_group_codes(self, personal_con):
        # apply_template rewrites category_groups.name, so capture the
        # pre-migration name keyed by id before that happens.
        before = {
            r["id"]: r["name"]
            for r in personal_con.execute("SELECT id, name FROM category_groups").fetchall()
        }

        category_seed.migrate_personal_catalog(personal_con)

        after = {
            r["id"]: r["code"]
            for r in personal_con.execute("SELECT id, code FROM category_groups").fetchall()
        }
        for group_id, name in before.items():
            assert after[group_id] == category_seed.GROUP_MAP[name]

    def test_sets_active_template(self, personal_con):
        category_seed.migrate_personal_catalog(personal_con)

        row = personal_con.execute(
            "SELECT value FROM app_metadata WHERE key = 'active_template'",
        ).fetchone()
        assert row["value"] == "active"

    def test_group_names_are_russian(self, personal_con):
        category_seed.migrate_personal_catalog(personal_con)

        names = {
            r["code"]: r["name"]
            for r in personal_con.execute("SELECT code, name FROM category_groups").fetchall()
        }
        assert names["food"] == "Еда"
        assert names["utilities"] == "ЖКХ и сервисы"
        assert names["sport"] == "Спорт"

    def test_second_call_is_noop(self, personal_con):
        category_seed.migrate_personal_catalog(personal_con)
        before = _snapshot(personal_con)

        category_seed.migrate_personal_catalog(personal_con)

        assert _snapshot(personal_con) == before

    def test_foreign_keys_intact_after_migration(self, personal_con):
        category_seed.migrate_personal_catalog(personal_con)

        problems = personal_con.execute("PRAGMA foreign_key_check").fetchall()
        assert problems == []


@allure.epic("Category templates")
@allure.feature("Personal catalog migration")
class TestBootstrapCategoriesDispatch:
    def test_personal_db_dispatches_to_migration(self, personal_con, monkeypatch):
        calls = []
        monkeypatch.setattr(
            category_seed,
            "migrate_personal_catalog",
            lambda con: calls.append(("migrate", con)),
        )
        monkeypatch.setattr(
            category_seed,
            "seed_category_templates",
            lambda con: calls.append(("seed", con)),
        )

        category_seed.bootstrap_categories(personal_con)

        assert [c[0] for c in calls] == ["migrate"]

    def test_fresh_db_dispatches_to_seed(self, db, monkeypatch):  # noqa: ARG001
        calls = []
        monkeypatch.setattr(
            category_seed,
            "migrate_personal_catalog",
            lambda con: calls.append(("migrate", con)),
        )
        monkeypatch.setattr(
            category_seed,
            "seed_category_templates",
            lambda con: calls.append(("seed", con)),
        )

        with storage.connection() as con:
            category_seed.bootstrap_categories(con)

        assert [c[0] for c in calls] == ["seed"]


@allure.epic("Category templates")
@allure.feature("Personal catalog migration")
class TestMigrationValidation:
    def test_unknown_category_name_raises_before_any_writes(self, db):  # noqa: ARG001
        with storage.connection() as con:
            with storage.transaction(con):
                con.execute(
                    "INSERT INTO category_groups (id, name, sort_order) VALUES (1, 'Еда', 1)",
                )
                con.execute(
                    "INSERT INTO categories (id, name, group_id, is_active)"
                    " VALUES (1, 'Неизвестная категория', 1, 1)",
                )

            with pytest.raises(ValueError, match="unrecognised category names"):
                category_seed.migrate_personal_catalog(con)

            category = con.execute("SELECT code FROM categories WHERE id = 1").fetchone()
            group = con.execute("SELECT code FROM category_groups WHERE id = 1").fetchone()
            assert category["code"] is None
            assert group["code"] is None
