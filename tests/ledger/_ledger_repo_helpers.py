"""Shared fixtures for the split ``test_ledger_repo_*.py`` files.

Underscore prefix keeps pytest from collecting this as a test module.
Each ``test_ledger_repo_*.py`` file imports the names it uses; pytest
treats them as fixtures because they're in the test module's namespace.

The autouse ``_tmp_data_dir`` is module-local (imported into each split
file rather than promoted to ``conftest.py``) so the per-test DB-path
override stays scoped to the ledger-repo suite — it must not leak into
sibling tests that point at the real ``DATA_DIR`` or run against a
mocked repo.
"""

import shutil

import pytest

from dinary.services import ledger_repo


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger_repo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ledger_repo, "DB_PATH", tmp_path / "dinary.db")


@pytest.fixture
def fresh_db(tmp_path, blank_db):
    shutil.copy(blank_db, tmp_path / "dinary.db")


@pytest.fixture
def populated_catalog(fresh_db):
    """Seed the catalog with a minimal 3D dataset."""
    con = ledger_repo.get_connection()
    try:
        con.execute(
            "INSERT INTO category_groups (id, name, sort_order, is_active)"
            " VALUES (1, 'Food', 1, 1)",
        )
        con.execute(
            "INSERT INTO categories (id, name, group_id, is_active) VALUES (1, 'еда', 1, 1)",
        )
        con.execute(
            "INSERT INTO categories (id, name, group_id, is_active) VALUES (2, 'кафе', 1, 1)",
        )
        con.execute(
            "INSERT INTO events (id, name, date_from, date_to,"
            " auto_attach_enabled, is_active)"
            " VALUES (10, 'отпуск-2026', '2026-01-01', '2026-12-31', 1, 1)",
        )
        con.execute("INSERT INTO tags (id, name, is_active) VALUES (1, 'собака', 1)")
        con.execute(
            "INSERT INTO tags (id, name, is_active) VALUES (2, 'релокация', 1)",
        )
        con.execute(
            "INSERT INTO import_mapping (id, year, sheet_category, sheet_group,"
            " category_id, event_id) VALUES (1, 0, 'еда', 'собака', 1, NULL)",
        )
        con.execute(
            "INSERT INTO import_mapping_tags (mapping_id, tag_id) VALUES (1, 1)",
        )
        con.execute(
            "INSERT INTO import_mapping (id, year, sheet_category, sheet_group,"
            " category_id, event_id) VALUES (2, 0, 'кафе', 'путешествия', 2, 10)",
        )
        con.execute(
            "INSERT INTO import_mapping (id, year, sheet_category, sheet_group,"
            " category_id, event_id) VALUES (3, 2026, 'еда', 'собака', 2, 10)",
        )
        con.commit()
    finally:
        con.close()


__all__ = ["data_dir", "fresh_db", "populated_catalog"]
