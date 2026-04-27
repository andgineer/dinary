"""PATCH-side ``catalog_writer`` tests.

Pin the per-call atomicity of ``edit_*`` calls (rename + flag toggle
in a single bump, full rollback on conflict, no half-applied
column updates) and the tag-usage guard the writer enforces while
editing event ``auto_tags``.

Cross-cutting invariants (version bump, reactivate column
preservation, integrity rules) live in
:file:`test_catalog_writer_invariants.py`.
"""

from datetime import date

import allure
import pytest

from dinary.services import catalog_writer, ledger_repo

from _catalog_writer_helpers import _DT, _seed_minimal, fresh_db  # noqa: F401


@allure.epic("CatalogWriter")
@allure.feature("Atomic PATCH")
class TestAtomicPatch:
    def test_edit_category_empty_string_clears_sheet_columns(self, fresh_db):
        """Empty-string sentinel on PATCH clears ``sheet_name`` /
        ``sheet_group`` back to NULL. Needed by the future in-app
        editor so an operator can remove a stale mapping without
        having to delete and re-add the category."""
        con = ledger_repo.get_connection()
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
        con = ledger_repo.get_connection()
        try:
            _seed_minimal(con)
            v0 = ledger_repo.get_catalog_version(con)
            catalog_writer.edit_category(
                con,
                1,
                name="food-renamed",
                is_active=False,
            )
            v1 = ledger_repo.get_catalog_version(con)
            row = con.execute(
                "SELECT name, is_active FROM categories WHERE id = 1",
            ).fetchone()
        finally:
            con.close()
        assert row[0] == "food-renamed"
        assert bool(row[1]) is False
        # Single PATCH -> single version bump, not two.
        assert v1 == v0 + 1

    def test_edit_category_rolls_back_on_conflict(self, fresh_db):
        """Renaming a row into a name that already exists must fail
        and must not commit any other column change in the same PATCH
        — the writer validates all inputs before any UPDATE so a
        partial commit is not possible."""
        con = ledger_repo.get_connection()
        try:
            _seed_minimal(con)
            # Sibling row the rename would collide with.
            con.execute(
                "INSERT INTO categories (id, name, group_id, is_active)"
                " VALUES (2, 'drink', 1, TRUE)",
            )
            v0 = ledger_repo.get_catalog_version(con)
            with pytest.raises(catalog_writer.CatalogConflictError):
                catalog_writer.edit_category(
                    con,
                    1,
                    name="drink",
                    is_active=False,
                )
            v1 = ledger_repo.get_catalog_version(con)
            row = con.execute(
                "SELECT name, is_active FROM categories WHERE id = 1",
            ).fetchone()
        finally:
            con.close()
        # Rename rejected -> the sibling is_active=False toggle in the
        # same call must not have landed either.
        assert row[0] == "food"
        assert bool(row[1]) is True
        assert v1 == v0

    def test_edit_event_rolls_back_partial_date_patch_on_bad_composite(
        self,
        fresh_db,
    ):
        con = ledger_repo.get_connection()
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
    def test_edit_event_accepts_inactive_tag_in_auto_tags(self, fresh_db):
        """Deactivating a tag must not block writes that reference it
        via event ``auto_tags``. The operator hides the tag from the
        ручной пикер via the Управлять list; the tag still exists in
        the ``tags`` table, so events and the map tab must keep
        resolving it by name.

        This pins the failure mode where hiding "отпуск" (a tag set
        only by vacation events, never manually) would trip 422 in
        ``_require_known_tag_names`` on every subsequent event-edit
        that still named it.
        """
        con = ledger_repo.get_connection()
        try:
            _seed_minimal(con)
            con.execute("INSERT INTO tags (id, name, is_active) VALUES (1, 'отпуск', FALSE)")
            con.execute(
                "INSERT INTO events"
                " (id, name, date_from, date_to, auto_attach_enabled, is_active, auto_tags)"
                " VALUES (1, 'trip', '2026-01-01', '2026-12-31', TRUE, TRUE, '[]')",
            )
            catalog_writer.edit_event(con, 1, auto_tags=["отпуск"])
            stored = con.execute("SELECT auto_tags FROM events WHERE id = 1").fetchone()
        finally:
            con.close()
        assert stored[0] == '["отпуск"]'

    def test_edit_event_still_rejects_unknown_tag_name(self, fresh_db):
        """The ``is_active`` gate was lifted, but the absent-from-table
        gate stays: a typo or a hard-deleted tag name still 422s so
        ``resolve_event_auto_tag_ids`` never silently drops at runtime.
        """
        con = ledger_repo.get_connection()
        try:
            _seed_minimal(con)
            con.execute(
                "INSERT INTO events"
                " (id, name, date_from, date_to, auto_attach_enabled, is_active, auto_tags)"
                " VALUES (1, 'trip', '2026-01-01', '2026-12-31', TRUE, TRUE, '[]')",
            )
            with pytest.raises(catalog_writer.CatalogWriteError, match="unknown tag name"):
                catalog_writer.edit_event(con, 1, auto_tags=["ghost_tag"])
        finally:
            con.close()

    def test_soft_retire_tag_used_by_expense_is_allowed(self, fresh_db):
        """Soft-retiring a tag still referenced by an expense is
        allowed (matches PATCH/DELETE symmetry). The expense keeps
        its tag_id row intact; the tag simply stops appearing in the
        ручной пикер. Event-driven auto-attach keeps working against
        inactive tags — that's the whole point of "hide from picker,
        keep as an event auto_tags anchor".
        """
        con = ledger_repo.get_connection()
        try:
            _seed_minimal(con)
            con.execute("INSERT INTO tags (id, name, is_active) VALUES (1, 't1', TRUE)")
            ledger_repo.insert_expense(
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
            catalog_writer.set_tag_active(con, 1, active=False)
            row = con.execute("SELECT is_active FROM tags WHERE id = 1").fetchone()
        finally:
            con.close()
        assert bool(row[0]) is False
