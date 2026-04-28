"""Shared fixtures and helpers for the split ``test_api_*.py`` files.

Underscore prefix keeps pytest from collecting this as a test module.
Each ``test_api_*.py`` file imports the names it uses; pytest treats
them as fixtures because they're in the test module's namespace.

The autouse ``_tmp_db`` is module-local rather than promoted to
``conftest.py`` so the per-test DB-path override stays scoped to the
API suite (sibling tests must not have their ``ledger_repo.DATA_DIR``
silently rewritten).
"""

import contextlib
import shutil
import threading
from decimal import Decimal
from unittest.mock import patch

import pytest

from dinary.services import ledger_repo


@contextlib.contextmanager
def _count_race_recoveries():
    """Count ``insert_expense`` race-recovery ROLLBACKs during a test run.

    Yields a ``{"count": int}`` dict that callers read *after* the
    ``with`` block exits. Works by wrapping
    ``ledger_repo.best_effort_rollback`` with a counter that
    increments on contexts containing ``"race-recovery"`` — the
    substring both INSERT-time and COMMIT-time ``_RACE_EXCS``
    branches embed in their context string. The outer
    ``except Exception: best_effort_rollback(...)`` in
    ``insert_expense`` uses a different context string and is not
    counted, so the counter is specific to the race-recovery path.

    A ``threading.Lock`` protects the increment because concurrent
    tests (``asyncio.to_thread`` + ``asyncio.gather``) dispatch each
    request to a ThreadPool worker; the GIL makes ``+= 1`` usually
    atomic, but this is the sort of instrumentation where a spurious
    off-by-one under future CPython changes would obscure exactly
    the scheduling-regression class we're trying to detect.
    """
    counter = {"count": 0}
    lock = threading.Lock()
    original = ledger_repo.best_effort_rollback

    def counting_rollback(con, *, context: str) -> None:
        if "race-recovery" in context:
            with lock:
                counter["count"] += 1
        original(con, context=context)

    with patch.object(ledger_repo, "best_effort_rollback", new=counting_rollback):
        yield counter


def _mock_get_rate(con, rate_date, source, target, *, offline=False):
    """Identity FX stub: rate=1 keeps stored ``amount`` equal to ``amount_original``."""
    return Decimal(1)


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch, blank_db):
    """Point the repo at a fresh per-test DB and seed a minimal catalog."""
    dst = tmp_path / "dinary.db"
    shutil.copy(blank_db, dst)
    monkeypatch.setattr(ledger_repo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ledger_repo, "DB_PATH", dst)

    con = ledger_repo.get_connection()
    try:
        con.execute(
            "INSERT INTO category_groups (id, name, sort_order, is_active)"
            " VALUES (1, 'Food', 1, TRUE)",
        )
        con.execute(
            "INSERT INTO category_groups (id, name, sort_order, is_active)"
            " VALUES (2, 'Transport', 2, TRUE)",
        )
        con.execute(
            "INSERT INTO categories (id, name, group_id, is_active) VALUES (1, 'еда', 1, TRUE)",
        )
        con.execute(
            "INSERT INTO categories (id, name, group_id, is_active)"
            " VALUES (2, 'транспорт', 2, TRUE)",
        )
        # Pre-filter-friendly inactive category: API must treat it as
        # unknown for both reads (list) and writes (POST).
        con.execute(
            "INSERT INTO categories (id, name, group_id, is_active)"
            " VALUES (3, 'ретро-категория', 1, FALSE)",
        )
        con.execute("INSERT INTO tags (id, name, is_active) VALUES (1, 'собака', TRUE)")
        con.execute("INSERT INTO tags (id, name, is_active) VALUES (2, 'аня', TRUE)")
        con.execute(
            "INSERT INTO events (id, name, date_from, date_to,"
            " auto_attach_enabled, is_active)"
            " VALUES (1, 'evt-2026', '2026-01-01', '2026-12-31', TRUE, TRUE)",
        )
    finally:
        con.close()


__all__ = ["_count_race_recoveries", "_mock_get_rate", "db"]
