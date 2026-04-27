"""Invariant tests for ``catalog_writer``.

Pin three groups of cross-cutting rules every mutation must obey:

* ``catalog_version`` bookkeeping — observable changes (add /
  reactivate / move) bump the counter exactly once; idempotent no-op
  rewrites do not.
* Reactivate preserves "optional" columns that the caller didn't
  explicitly touch (``sheet_name``, ``sheet_group``,
  ``date_from``/``date_to``, ``auto_attach_enabled``) so a reactivate
  request body can stay minimal.
* Integrity rules — name collisions are 409; soft-retire of
  referenced rows is 200 (matches ``DELETE``); event ranges must
  satisfy ``date_from <= date_to``; cross-group reactivate moves
  the row only when the existing match is inactive.

PATCH-side fine-grain rollback / partial-update semantics live in
:file:`test_catalog_writer_patch.py`.
"""

from datetime import date

import allure
import pytest

from dinary.services import catalog_writer, ledger_repo

from _catalog_writer_helpers import _DT, _seed_minimal, fresh_db  # noqa: F401


@allure.epic("CatalogWriter")
@allure.feature("Version bump invariants")
class TestVersionBump:
    def test_add_group_bumps_version(self, fresh_db):
        con = ledger_repo.get_connection()
        try:
            v0 = ledger_repo.get_catalog_version(con)
            result = catalog_writer.add_group(con, name="new")
            v1 = ledger_repo.get_catalog_version(con)
        finally:
            con.close()
        assert v1 == v0 + 1
        assert result.status == "created"
        assert result.id > 0

    def test_idempotent_add_of_existing_active_group_is_noop(self, fresh_db):
        con = ledger_repo.get_connection()
        try:
            _seed_minimal(con)
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order, is_active)"
                " VALUES (2, 'already_here', 2, TRUE)",
            )
            v1 = ledger_repo.get_catalog_version(con)
            result = catalog_writer.add_group(con, name="already_here")
            v2 = ledger_repo.get_catalog_version(con)
        finally:
            con.close()
        assert v2 == v1
        assert result.status == "noop"
        assert result.id == 2

    def test_reactivate_inactive_group_bumps(self, fresh_db):
        con = ledger_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order, is_active)"
                " VALUES (1, 'retired', 1, FALSE)",
            )
            v0 = ledger_repo.get_catalog_version(con)
            result = catalog_writer.add_group(con, name="retired")
            v1 = ledger_repo.get_catalog_version(con)
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
        con = ledger_repo.get_connection()
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
        con = ledger_repo.get_connection()
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
    def test_soft_retire_referenced_category_is_allowed(self, fresh_db):
        """``set_category_active(False)`` on a referenced category is
        allowed (soft-retire). This mirrors the ``DELETE`` endpoint's
        soft-delete behaviour so PATCH and DELETE don't disagree on
        the same end-state; see ``edit_category`` docstring."""
        con = ledger_repo.get_connection()
        try:
            _seed_minimal(con)
            ledger_repo.insert_expense(
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
            catalog_writer.set_category_active(con, 1, active=False)
            row = con.execute(
                "SELECT is_active FROM categories WHERE id = 1",
            ).fetchone()
        finally:
            con.close()
        assert bool(row[0]) is False

    def test_cannot_rename_into_existing_name(self, fresh_db):
        con = ledger_repo.get_connection()
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
        con = ledger_repo.get_connection()
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

    def test_add_event_reactivate_still_validates_inverted_incoming_dates(self, fresh_db):
        """Reactivating an existing-but-inactive event must 422 on an
        inverted ``date_from``/``date_to`` body, even though the
        reactivate branch never actually applies those values to the
        stored row.

        Rationale: ``add_event`` treats the request body as an assertion
        of intent ("I want an event with these fields"), and honouring a
        body we know is invalid would let an operator ship garbage under
        a valid-looking 200 response. Keeping validation symmetric with
        ``edit_event`` also means the PWA sees the same 422 whether it
        hits the add-or-reactivate path or the dedicated edit endpoint.
        Stored fields stay frozen on reactivate (see docstring) — the
        caller must use ``edit_event`` to actually change them."""
        con = ledger_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO events"
                " (id, name, date_from, date_to, auto_attach_enabled, is_active)"
                " VALUES (1, 'отпуск-2024', '2024-06-01', '2024-06-30', FALSE, FALSE)",
            )
            with pytest.raises(catalog_writer.CatalogWriteError):
                catalog_writer.add_event(
                    con,
                    name="отпуск-2024",
                    date_from=date(2030, 1, 2),
                    date_to=date(2030, 1, 1),
                )
            # Row remains unchanged (inactive, original dates intact).
            row = con.execute(
                "SELECT date_from, date_to, is_active FROM events WHERE id = 1",
            ).fetchone()
        finally:
            con.close()
        assert row[0].isoformat() == "2024-06-01"
        assert row[1].isoformat() == "2024-06-30"
        assert bool(row[2]) is False

    def test_add_category_active_cross_group_is_conflict(self, fresh_db):
        """Calling add_category on a name that is already active in a
        different group must 409, not silently move the row. The
        operator has to use edit_category(group_id=...) for an
        intentional relocation."""
        con = ledger_repo.get_connection()
        try:
            _seed_minimal(con)
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order, is_active)"
                " VALUES (2, 'g2', 2, TRUE)",
            )
            v0 = ledger_repo.get_catalog_version(con)
            with pytest.raises(catalog_writer.CatalogConflictError):
                catalog_writer.add_category(con, name="food", group_id=2)
            v1 = ledger_repo.get_catalog_version(con)
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
        con = ledger_repo.get_connection()
        try:
            _seed_minimal(con)
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order, is_active)"
                " VALUES (2, 'g2', 2, TRUE)",
            )
            con.execute(
                "UPDATE categories SET is_active = FALSE WHERE id = 1",
            )
            v0 = ledger_repo.get_catalog_version(con)
            result = catalog_writer.add_category(con, name="food", group_id=2)
            v1 = ledger_repo.get_catalog_version(con)
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
