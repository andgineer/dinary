"""Tests for ``catalog_writer`` — the admin-API write path for catalog tables.

Every mutation must:

* Run inside a single DuckDB transaction (COMMIT or ROLLBACK, never
  leave dangling state).
* Conditionally bump ``catalog_version`` using a canonical-state hash:
  observable changes bump, no-op rewrites don't.
* Refuse to soft-delete a row still referenced by any ``expenses``
  (or ``expense_tags``) row.
* Refuse to rename into a name already in use.

These tests pin the invariants the PWA catalog cache relies on.
"""

from datetime import date, datetime

import allure
import pytest

from dinary.services import catalog_writer, duckdb_repo

_DT = datetime(2026, 4, 20, 10, 0, 0)


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(duckdb_repo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(duckdb_repo, "DB_PATH", tmp_path / "dinary.duckdb")
    duckdb_repo.init_db()


def _seed_minimal(con):
    con.execute(
        "INSERT INTO category_groups (id, name, sort_order, is_active) VALUES (1, 'g1', 1, TRUE)",
    )
    con.execute(
        "INSERT INTO categories (id, name, group_id, is_active) VALUES (1, 'food', 1, TRUE)",
    )


@allure.epic("CatalogWriter")
@allure.feature("Version bump invariants")
class TestVersionBump:
    def test_add_group_bumps_version(self, fresh_db):
        con = duckdb_repo.get_connection()
        try:
            v0 = duckdb_repo.get_catalog_version(con)
            result = catalog_writer.add_group(con, name="new")
            v1 = duckdb_repo.get_catalog_version(con)
        finally:
            con.close()
        assert v1 == v0 + 1
        assert result.status == "created"
        assert result.id > 0

    def test_idempotent_add_of_existing_active_group_is_noop(self, fresh_db):
        con = duckdb_repo.get_connection()
        try:
            _seed_minimal(con)
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order, is_active)"
                " VALUES (2, 'already_here', 2, TRUE)",
            )
            v1 = duckdb_repo.get_catalog_version(con)
            result = catalog_writer.add_group(con, name="already_here")
            v2 = duckdb_repo.get_catalog_version(con)
        finally:
            con.close()
        assert v2 == v1
        assert result.status == "noop"
        assert result.id == 2

    def test_reactivate_inactive_group_bumps(self, fresh_db):
        con = duckdb_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order, is_active)"
                " VALUES (1, 'retired', 1, FALSE)",
            )
            v0 = duckdb_repo.get_catalog_version(con)
            result = catalog_writer.add_group(con, name="retired")
            v1 = duckdb_repo.get_catalog_version(con)
            row = con.execute(
                "SELECT is_active FROM category_groups WHERE id = ?",
                [result.id],
            ).fetchone()
        finally:
            con.close()
        assert result.id == 1
        assert result.status == "reactivated"
        assert bool(row[0]) is True
        assert v1 == v0 + 1


@allure.epic("CatalogWriter")
@allure.feature("Reactivate preserves optional columns")
class TestReactivatePreserves:
    def test_add_category_reactivate_preserves_sheet_columns(self, fresh_db):
        con = duckdb_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order, is_active)"
                " VALUES (1, 'g1', 1, TRUE)",
            )
            con.execute(
                "INSERT INTO categories"
                " (id, name, group_id, is_active, sheet_name, sheet_group)"
                " VALUES (1, 'food', 1, FALSE, 'custom_sheet_name', 'custom_group')",
            )
            # Call add_category without sheet_name/sheet_group --
            # previous values must survive the reactivate.
            result = catalog_writer.add_category(con, name="food", group_id=1)
            row = con.execute(
                "SELECT is_active, sheet_name, sheet_group FROM categories WHERE id = 1",
            ).fetchone()
        finally:
            con.close()
        assert result.id == 1
        assert result.status == "reactivated"
        assert bool(row[0]) is True
        assert row[1] == "custom_sheet_name"
        assert row[2] == "custom_group"

    def test_add_event_reactivate_preserves_dates_and_auto_attach(self, fresh_db):
        con = duckdb_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO events"
                " (id, name, date_from, date_to, auto_attach_enabled, is_active)"
                " VALUES (1, 'отпуск-2024', '2024-06-01', '2024-06-30', TRUE, FALSE)",
            )
            # Re-add with "default" dates; existing dates must not be
            # overwritten because the caller didn't explicitly PATCH them.
            result = catalog_writer.add_event(
                con,
                name="отпуск-2024",
                date_from=date(2026, 1, 1),
                date_to=date(2026, 1, 1),
                auto_attach_enabled=False,
            )
            row = con.execute(
                "SELECT date_from, date_to, auto_attach_enabled, is_active"
                " FROM events WHERE id = 1",
            ).fetchone()
        finally:
            con.close()
        assert result.status == "reactivated"
        assert row[0].isoformat() == "2024-06-01"
        assert row[1].isoformat() == "2024-06-30"
        assert bool(row[2]) is True
        assert bool(row[3]) is True


@allure.epic("CatalogWriter")
@allure.feature("Integrity rules")
class TestIntegrityRules:
    def test_cannot_soft_delete_referenced_category(self, fresh_db):
        con = duckdb_repo.get_connection()
        try:
            _seed_minimal(con)
            duckdb_repo.insert_expense(
                con,
                client_expense_id="pin-cat",
                expense_datetime=_DT,
                amount=1.0,
                amount_original=1.0,
                currency_original="RSD",
                category_id=1,
                event_id=None,
                comment="",
                sheet_category=None,
                sheet_group=None,
                tag_ids=[],
                enqueue_logging=False,
            )
            with pytest.raises(catalog_writer.CatalogInUseError):
                catalog_writer.set_category_active(con, 1, active=False)
        finally:
            con.close()

    def test_cannot_rename_into_existing_name(self, fresh_db):
        con = duckdb_repo.get_connection()
        try:
            _seed_minimal(con)
            con.execute(
                "INSERT INTO categories (id, name, group_id, is_active)"
                " VALUES (2, 'drink', 1, TRUE)",
            )
            with pytest.raises(catalog_writer.CatalogConflictError):
                catalog_writer.edit_category(con, 2, name="food")
        finally:
            con.close()

    def test_event_date_from_must_be_le_date_to(self, fresh_db):
        con = duckdb_repo.get_connection()
        try:
            with pytest.raises(catalog_writer.CatalogWriteError):
                catalog_writer.add_event(
                    con,
                    name="bad",
                    date_from=date(2026, 6, 1),
                    date_to=date(2026, 5, 1),
                )
        finally:
            con.close()

    def test_add_event_reactivate_ignores_inverted_incoming_dates(self, fresh_db):
        """Reactivating an existing-but-inactive event must not reject
        inverted ``date_from``/``date_to`` on the incoming payload,
        because those values are discarded on the reactivate branch.
        The stored row's range stayed well-formed at the time it was
        originally created."""
        con = duckdb_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO events"
                " (id, name, date_from, date_to, auto_attach_enabled, is_active)"
                " VALUES (1, 'отпуск-2024', '2024-06-01', '2024-06-30', FALSE, FALSE)",
            )
            # Dates here are deliberately inverted and would 422 on a
            # fresh insert — but this path never applies them.
            result = catalog_writer.add_event(
                con,
                name="отпуск-2024",
                date_from=date(2030, 1, 2),
                date_to=date(2030, 1, 1),
            )
            row = con.execute(
                "SELECT date_from, date_to, is_active FROM events WHERE id = 1",
            ).fetchone()
        finally:
            con.close()
        assert result.status == "reactivated"
        assert row[0].isoformat() == "2024-06-01"
        assert row[1].isoformat() == "2024-06-30"
        assert bool(row[2]) is True

    def test_add_category_active_cross_group_is_conflict(self, fresh_db):
        """Calling add_category on a name that is already active in a
        different group must 409, not silently move the row. The
        operator has to use edit_category(group_id=...) for an
        intentional relocation."""
        con = duckdb_repo.get_connection()
        try:
            _seed_minimal(con)
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order, is_active)"
                " VALUES (2, 'g2', 2, TRUE)",
            )
            v0 = duckdb_repo.get_catalog_version(con)
            with pytest.raises(catalog_writer.CatalogConflictError):
                catalog_writer.add_category(con, name="food", group_id=2)
            v1 = duckdb_repo.get_catalog_version(con)
            row = con.execute(
                "SELECT group_id, is_active FROM categories WHERE id = 1",
            ).fetchone()
        finally:
            con.close()
        assert int(row[0]) == 1
        assert bool(row[1]) is True
        assert v1 == v0

    def test_add_category_inactive_cross_group_reactivates_and_moves(self, fresh_db):
        """An *inactive* match in a different group is legitimately
        reactivated and moved — the row wasn't serving any user-visible
        purpose, and the add action's group_id is authoritative."""
        con = duckdb_repo.get_connection()
        try:
            _seed_minimal(con)
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order, is_active)"
                " VALUES (2, 'g2', 2, TRUE)",
            )
            con.execute(
                "UPDATE categories SET is_active = FALSE WHERE id = 1",
            )
            v0 = duckdb_repo.get_catalog_version(con)
            result = catalog_writer.add_category(con, name="food", group_id=2)
            v1 = duckdb_repo.get_catalog_version(con)
            row = con.execute(
                "SELECT group_id, is_active FROM categories WHERE id = 1",
            ).fetchone()
        finally:
            con.close()
        assert result.id == 1
        assert result.status == "reactivated"
        assert int(row[0]) == 2
        assert bool(row[1]) is True
        assert v1 == v0 + 1


@allure.epic("CatalogWriter")
@allure.feature("Atomic PATCH")
class TestAtomicPatch:
    def test_edit_category_empty_string_clears_sheet_columns(self, fresh_db):
        """Empty-string sentinel on PATCH clears ``sheet_name`` /
        ``sheet_group`` back to NULL. Needed by the future in-app
        editor so an operator can remove a stale mapping without
        having to delete and re-add the category."""
        con = duckdb_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order, is_active)"
                " VALUES (1, 'g1', 1, TRUE)",
            )
            con.execute(
                "INSERT INTO categories"
                " (id, name, group_id, is_active, sheet_name, sheet_group)"
                " VALUES (1, 'food', 1, TRUE, 'legacy_name', 'legacy_group')",
            )
            catalog_writer.edit_category(
                con,
                1,
                sheet_name="",
                sheet_group="",
            )
            row = con.execute(
                "SELECT sheet_name, sheet_group FROM categories WHERE id = 1",
            ).fetchone()
        finally:
            con.close()
        assert row[0] is None
        assert row[1] is None

    def test_edit_category_applies_name_and_deactivate_in_one_tx(self, fresh_db):
        con = duckdb_repo.get_connection()
        try:
            _seed_minimal(con)
            v0 = duckdb_repo.get_catalog_version(con)
            catalog_writer.edit_category(
                con,
                1,
                name="food-renamed",
                is_active=False,
            )
            v1 = duckdb_repo.get_catalog_version(con)
            row = con.execute(
                "SELECT name, is_active FROM categories WHERE id = 1",
            ).fetchone()
        finally:
            con.close()
        assert row[0] == "food-renamed"
        assert bool(row[1]) is False
        # Single PATCH -> single version bump, not two.
        assert v1 == v0 + 1

    def test_edit_category_rolls_back_on_inuse_failure(self, fresh_db):
        """Renaming a row and simultaneously trying to deactivate it
        while it's still referenced must not commit the rename."""
        con = duckdb_repo.get_connection()
        try:
            _seed_minimal(con)
            duckdb_repo.insert_expense(
                con,
                client_expense_id="pin",
                expense_datetime=_DT,
                amount=1.0,
                amount_original=1.0,
                currency_original="RSD",
                category_id=1,
                event_id=None,
                comment="",
                sheet_category=None,
                sheet_group=None,
                tag_ids=[],
                enqueue_logging=False,
            )
            v0 = duckdb_repo.get_catalog_version(con)
            with pytest.raises(catalog_writer.CatalogInUseError):
                catalog_writer.edit_category(
                    con,
                    1,
                    name="should-not-stick",
                    is_active=False,
                )
            v1 = duckdb_repo.get_catalog_version(con)
            row = con.execute(
                "SELECT name, is_active FROM categories WHERE id = 1",
            ).fetchone()
        finally:
            con.close()
        # Rename must have been rolled back because the deactivate
        # failed in the same transaction.
        assert row[0] == "food"
        assert bool(row[1]) is True
        assert v1 == v0

    def test_edit_event_rolls_back_partial_date_patch_on_bad_composite(
        self,
        fresh_db,
    ):
        con = duckdb_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO events"
                " (id, name, date_from, date_to, auto_attach_enabled, is_active)"
                " VALUES (1, 'ev', '2026-01-01', '2026-12-31', FALSE, TRUE)",
            )
            # Attempt to move date_from past existing date_to; must 422
            # and leave the row untouched.
            with pytest.raises(catalog_writer.CatalogWriteError):
                catalog_writer.edit_event(
                    con,
                    1,
                    date_from=date(2027, 1, 1),
                )
            row = con.execute(
                "SELECT date_from, date_to FROM events WHERE id = 1",
            ).fetchone()
        finally:
            con.close()
        assert row[0].isoformat() == "2026-01-01"
        assert row[1].isoformat() == "2026-12-31"


@allure.epic("CatalogWriter")
@allure.feature("Tag usage guard")
class TestTagUsage:
    def test_cannot_deactivate_tag_used_by_expense(self, fresh_db):
        con = duckdb_repo.get_connection()
        try:
            _seed_minimal(con)
            con.execute("INSERT INTO tags (id, name, is_active) VALUES (1, 't1', TRUE)")
            duckdb_repo.insert_expense(
                con,
                client_expense_id="tag-pin",
                expense_datetime=_DT,
                amount=1.0,
                amount_original=1.0,
                currency_original="RSD",
                category_id=1,
                event_id=None,
                comment="",
                sheet_category=None,
                sheet_group=None,
                tag_ids=[1],
                enqueue_logging=False,
            )
            with pytest.raises(catalog_writer.CatalogInUseError):
                catalog_writer.set_tag_active(con, 1, active=False)
        finally:
            con.close()
