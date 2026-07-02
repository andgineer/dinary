"""PATCH-side ``catalog_writer`` tests.

Pin the per-call atomicity of ``edit_event`` (rolls back a partial
date update on a bad composite range) and the tag-usage guard the
writer enforces while editing event ``auto_tags``.

Cross-cutting invariants (version bump, reactivate column
preservation, integrity rules) live in
:file:`test_catalog_writer_invariants.py`.
"""

from datetime import date

import allure
import pytest

from dinary.db import storage
from dinary.api.controllers.catalog_writer_errors import CatalogWriteError
from dinary.api.controllers.catalog_writer_events import edit_event, set_tag_active
from dinary.db.expenses import ExpensePayload, insert_expense

from _catalog_writer_helpers import _DT, _seed_minimal, fresh_db  # noqa: F401


@allure.epic("Catalog")
@allure.feature("DB writer")
class TestAtomicPatch:
    def test_edit_event_rolls_back_partial_date_patch_on_bad_composite(
        self,
        fresh_db,
    ):
        con = storage.get_connection()
        try:
            con.execute(
                "INSERT INTO events"
                " (id, name, date_from, date_to, auto_attach_enabled, is_active)"
                " VALUES (1, 'ev', '2026-01-01', '2026-12-31', FALSE, TRUE)",
            )
            # Attempt to move date_from past existing date_to; must 422
            # and leave the row untouched.
            with pytest.raises(CatalogWriteError):
                edit_event(
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


@allure.epic("Catalog")
@allure.feature("DB writer")
class TestTagUsage:
    def test_edit_event_accepts_inactive_tag_in_auto_tags(self, fresh_db):
        """Deactivating a tag must not block writes that reference it
        via event ``auto_tags``. ``is_active=FALSE`` means "hide from the
        picker"; the ID is still valid for auto-attach.
        """
        con = storage.get_connection()
        try:
            _seed_minimal(con)
            con.execute("INSERT INTO tags (id, name, is_active) VALUES (1, 'vacation', FALSE)")
            con.execute(
                "INSERT INTO events"
                " (id, name, date_from, date_to, auto_attach_enabled, is_active, auto_tags)"
                " VALUES (1, 'trip', '2026-01-01', '2026-12-31', TRUE, TRUE, '[]')",
            )
            edit_event(con, 1, auto_tags=[1])
            stored = con.execute("SELECT auto_tags FROM events WHERE id = 1").fetchone()
        finally:
            con.close()
        assert stored[0] == "[1]"

    def test_edit_event_still_rejects_unknown_tag_id(self, fresh_db):
        con = storage.get_connection()
        try:
            _seed_minimal(con)
            con.execute(
                "INSERT INTO events"
                " (id, name, date_from, date_to, auto_attach_enabled, is_active, auto_tags)"
                " VALUES (1, 'trip', '2026-01-01', '2026-12-31', TRUE, TRUE, '[]')",
            )
            with pytest.raises(CatalogWriteError, match="unknown tag"):
                edit_event(con, 1, auto_tags=[9999])
        finally:
            con.close()

    def test_soft_retire_tag_used_by_expense_is_allowed(self, fresh_db):
        """Soft-retiring a used tag is allowed (matches PATCH/DELETE symmetry);
        auto-attach keeps working against inactive tags."""
        con = storage.get_connection()
        try:
            _seed_minimal(con)
            con.execute("INSERT INTO tags (id, name, is_active) VALUES (1, 't1', TRUE)")
            insert_expense(
                con,
                ExpensePayload(
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
                ),
                enqueue_logging=False,
            )
            set_tag_active(con, 1, active=False)
            row = con.execute("SELECT is_active FROM tags WHERE id = 1").fetchone()
        finally:
            con.close()
        assert bool(row[0]) is False
