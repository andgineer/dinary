"""Not promoted to conftest.py: keeps the per-test DB-path override scoped to this suite."""

import contextlib
import shutil
import threading
from decimal import Decimal
from unittest.mock import patch

import pytest

from dinary.db import expenses, storage


@contextlib.contextmanager
def _count_race_recoveries():
    """Matches on the "race-recovery" substring in the rollback context to isolate that path from other rollback causes."""
    counter = {"count": 0}
    lock = threading.Lock()
    original = storage.best_effort_rollback

    def counting_rollback(con, *, context: str) -> None:
        if "race-recovery" in context:
            with lock:
                counter["count"] += 1
        original(con, context=context)

    with patch.object(expenses, "best_effort_rollback", new=counting_rollback):
        yield counter


def _mock_get_rate(con, rate_date, source, target, *, offline=False):
    """Identity FX stub: rate=1 keeps stored ``amount`` equal to ``amount_original``."""
    return Decimal(1)


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch, blank_db):
    dst = tmp_path / "dinary.db"
    shutil.copy(blank_db, dst)
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "DB_PATH", dst)

    con = storage.get_connection()
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
            "INSERT INTO categories (id, name, group_id, is_active, code)"
            " VALUES (1, 'food', 1, TRUE, 'groceries')",
        )
        con.execute(
            "INSERT INTO categories (id, name, group_id, is_active, code)"
            " VALUES (2, 'transit', 2, TRUE, 'transit')",
        )
        # Inactive (not hidden/retired) category: a write referencing it
        # exercises activation-on-use (db.catalog.activate_category), which
        # requires a `code`.
        con.execute(
            "INSERT INTO categories (id, name, group_id, is_active, code, is_hidden, is_retired)"
            " VALUES (3, 'retro-category', 1, FALSE, 'retro_category', FALSE, FALSE)",
        )
        con.execute("INSERT INTO tags (id, name, is_active) VALUES (1, 'dog', TRUE)")
        con.execute("INSERT INTO tags (id, name, is_active) VALUES (2, 'anya', TRUE)")
        con.execute(
            "INSERT INTO events (id, name, date_from, date_to,"
            " auto_attach_enabled, is_active)"
            " VALUES (1, 'evt-2026', '2026-01-01', '2026-12-31', TRUE, TRUE)",
        )
    finally:
        con.close()


__all__ = ["_count_race_recoveries", "_mock_get_rate", "db"]
