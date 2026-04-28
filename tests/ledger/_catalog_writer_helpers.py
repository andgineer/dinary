"""Shared fixtures + seed helpers for the split
``test_catalog_writer_*.py`` files.

Underscore prefix keeps pytest from collecting this as a test
module. Per-suite scope (rather than ``conftest.py`` promotion) so
the per-call SQLite path override does not bleed into sibling
non-catalog suites that have their own DB autouse machinery.
"""

import shutil
from datetime import datetime

import pytest

from dinary.services import ledger_repo

_DT = datetime(2026, 4, 20, 10, 0, 0)


@pytest.fixture
def fresh_db(tmp_path, monkeypatch, blank_db):
    dst = tmp_path / "dinary.db"
    shutil.copy(blank_db, dst)
    monkeypatch.setattr(ledger_repo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ledger_repo, "DB_PATH", dst)


def _seed_minimal(con):
    con.execute(
        "INSERT INTO category_groups (id, name, sort_order, is_active) VALUES (1, 'g1', 1, TRUE)",
    )
    con.execute(
        "INSERT INTO categories (id, name, group_id, is_active) VALUES (1, 'food', 1, TRUE)",
    )


__all__ = ["_DT", "_seed_minimal", "fresh_db"]
