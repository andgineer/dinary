import shutil

import allure
import pytest

from dinary.services.classification_rules import classify_by_rules, create_or_update_rule
from dinary.services import db_migrations, ledger_repo


@pytest.fixture
def conn(tmp_path, monkeypatch):
    import unittest.mock
    import sqlite3

    dst = tmp_path / "dinary.db"
    blank_src = tmp_path / "blank.db"

    def _migration_connect(self, dburi):
        con = sqlite3.connect(str(self.uri.database), isolation_level=None)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=5000")
        return con

    with unittest.mock.patch.object(db_migrations.SQLiteBackend, "connect", _migration_connect):
        db_migrations.migrate_db(blank_src)

    shutil.copy(blank_src, dst)
    monkeypatch.setattr(ledger_repo, "DB_PATH", dst)
    monkeypatch.setattr(ledger_repo, "DATA_DIR", tmp_path)

    c = ledger_repo.get_connection()
    c.execute("INSERT INTO category_groups (id, name, sort_order) VALUES (1, 'Food', 1)")
    c.execute("INSERT INTO categories (id, name, group_id) VALUES (1, 'Groceries', 1)")
    c.execute("INSERT INTO categories (id, name, group_id) VALUES (2, 'Drinks', 1)")
    c.execute("INSERT INTO stores (id, chain_name) VALUES (1, 'Lidl')")
    yield c
    c.close()


@allure.epic("Services")
@allure.feature("Classification Rules")
class TestClassifyByRules:
    def test_miss_returns_none(self, conn):
        result = classify_by_rules(conn, 1, "jabuka")
        assert result is None

    def test_store_specific_rule_hit(self, conn):
        create_or_update_rule(conn, 1, "jabuka", 1, 3, "llm")
        result = classify_by_rules(conn, 1, "jabuka")
        assert result == (1, 3)

    def test_generic_rule_hit(self, conn):
        create_or_update_rule(conn, None, "hleb", 1, 4, "user_correction")
        result = classify_by_rules(conn, 1, "hleb")
        assert result == (1, 4)

    def test_store_specific_beats_generic(self, conn):
        create_or_update_rule(conn, None, "mleko", 1, 2, "llm")
        create_or_update_rule(conn, 1, "mleko", 2, 4, "user_correction")
        result = classify_by_rules(conn, 1, "mleko")
        assert result == (2, 4)

    def test_generic_rule_applies_to_different_store(self, conn):
        conn.execute("INSERT INTO stores (id, chain_name) VALUES (2, 'Maxi')")
        create_or_update_rule(conn, None, "sir", 1, 3, "llm")
        result = classify_by_rules(conn, 2, "sir")
        assert result == (1, 3)

    def test_no_store_id_miss(self, conn):
        create_or_update_rule(conn, 1, "jogurt", 1, 3, "llm")
        result = classify_by_rules(conn, None, "jogurt")
        assert result is None

    def test_no_store_id_generic_hit(self, conn):
        create_or_update_rule(conn, None, "jogurt", 2, 3, "llm")
        result = classify_by_rules(conn, None, "jogurt")
        assert result == (2, 3)


@allure.epic("Services")
@allure.feature("Classification Rules")
class TestCreateOrUpdateRule:
    def test_insert_new_rule(self, conn):
        create_or_update_rule(conn, 1, "banana", 1, 3, "llm")
        result = classify_by_rules(conn, 1, "banana")
        assert result == (1, 3)

    def test_update_existing_rule(self, conn):
        create_or_update_rule(conn, 1, "banana", 1, 3, "llm")
        create_or_update_rule(conn, 1, "banana", 2, 4, "user_correction")
        result = classify_by_rules(conn, 1, "banana")
        assert result == (2, 4)

    def test_user_correction_always_conf4(self, conn):
        create_or_update_rule(conn, 1, "sladoled", 1, 2, "user_correction")
        result = classify_by_rules(conn, 1, "sladoled")
        assert result[1] == 4

    def test_generic_and_store_rules_independent(self, conn):
        create_or_update_rule(conn, None, "voda", 2, 3, "llm")
        create_or_update_rule(conn, 1, "voda", 1, 4, "user_correction")
        assert classify_by_rules(conn, None, "voda") == (2, 3)
        assert classify_by_rules(conn, 1, "voda") == (1, 4)
