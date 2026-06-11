"""Invariant tests for ``catalog_writer``.

Pin two groups of cross-cutting rules every mutation must obey:

* ``catalog_version`` bookkeeping — observable changes (add /
  reactivate) bump the counter exactly once; idempotent no-op
  rewrites do not.
* Reactivate preserves "optional" columns that the caller didn't
  explicitly touch (``date_from``/``date_to``, ``auto_attach_enabled``)
  so a reactivate request body can stay minimal.
* Integrity rules — event ranges must satisfy ``date_from <= date_to``,
  including on the reactivate path.

PATCH-side fine-grain rollback / partial-update semantics live in
:file:`test_catalog_writer_patch.py`.
"""

from datetime import date

import allure
import pytest

from dinary.db import storage
from dinary.db.catalog import get_catalog_version
from dinary.api.controllers.catalog_writer_errors import CatalogWriteError
from dinary.api.controllers.catalog_writer_events import add_event
from dinary.api.controllers.catalog_writer_groups import add_group

from _catalog_writer_helpers import _seed_minimal, fresh_db  # noqa: F401


@allure.epic("Catalog")
@allure.feature("DB writer")
class TestVersionBump:
    def test_add_group_bumps_version(self, fresh_db):
        con = storage.get_connection()
        try:
            v0 = get_catalog_version(con)
            result = add_group(con, name="new")
            v1 = get_catalog_version(con)
        finally:
            con.close()
        assert v1 == v0 + 1
        assert result.status == "created"
        assert result.id > 0

    def test_idempotent_add_of_existing_active_group_is_noop(self, fresh_db):
        con = storage.get_connection()
        try:
            _seed_minimal(con)
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order, is_active)"
                " VALUES (2, 'already_here', 2, TRUE)",
            )
            v1 = get_catalog_version(con)
            result = add_group(con, name="already_here")
            v2 = get_catalog_version(con)
        finally:
            con.close()
        assert v2 == v1
        assert result.status == "noop"
        assert result.id == 2

    def test_reactivate_inactive_group_bumps(self, fresh_db):
        con = storage.get_connection()
        try:
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order, is_active)"
                " VALUES (1, 'retired', 1, FALSE)",
            )
            v0 = get_catalog_version(con)
            result = add_group(con, name="retired")
            v1 = get_catalog_version(con)
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


@allure.epic("Catalog")
@allure.feature("DB writer")
class TestReactivatePreserves:
    def test_add_event_reactivate_preserves_dates_and_auto_attach(self, fresh_db):
        con = storage.get_connection()
        try:
            con.execute(
                "INSERT INTO events"
                " (id, name, date_from, date_to, auto_attach_enabled, is_active)"
                " VALUES (1, 'отпуск-2024', '2024-06-01', '2024-06-30', TRUE, FALSE)",
            )
            # Re-add with "default" dates; existing dates must not be
            # overwritten because the caller didn't explicitly PATCH them.
            result = add_event(
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


@allure.epic("Catalog")
@allure.feature("DB writer")
class TestIntegrityRules:
    def test_event_date_from_must_be_le_date_to(self, fresh_db):
        con = storage.get_connection()
        try:
            with pytest.raises(CatalogWriteError):
                add_event(
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
        con = storage.get_connection()
        try:
            con.execute(
                "INSERT INTO events"
                " (id, name, date_from, date_to, auto_attach_enabled, is_active)"
                " VALUES (1, 'отпуск-2024', '2024-06-01', '2024-06-30', FALSE, FALSE)",
            )
            with pytest.raises(CatalogWriteError):
                add_event(
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
