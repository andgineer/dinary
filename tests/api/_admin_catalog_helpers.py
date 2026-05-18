"""Shared autouse fixture for the split ``test_admin_catalog_*.py``
files.

Each test gets a per-call temporary SQLite DB pre-seeded with a
single category group (id=1) so the split files can ``POST`` new
categories under it without first issuing a setup request. Pytest
auto-discovers this module via the per-test ``noqa: F401`` import
in each split file (we deliberately avoid promoting it to
``conftest.py`` so the override does not bleed into sibling
non-admin suites).
"""

import shutil

import pytest

from dinary.db import storage


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
    finally:
        con.close()


__all__ = ["db"]
