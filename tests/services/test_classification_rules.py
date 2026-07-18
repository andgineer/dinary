import json
import shutil

import allure
import pytest

from dinary.db.classification_rules import (
    RuleHit,
    RuleSpec,
    classify_by_rules,
    create_or_update_rule,
)
from dinary.db import db_migrations, storage


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
    monkeypatch.setattr(storage, "DB_PATH", dst)
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)

    c = storage.get_connection()
    c.execute("INSERT INTO category_groups (id, name, sort_order) VALUES (1, 'Food', 1)")
    c.execute("INSERT INTO categories (id, name, group_id) VALUES (1, 'Groceries', 1)")
    c.execute("INSERT INTO categories (id, name, group_id) VALUES (2, 'Drinks', 1)")
    c.execute("INSERT INTO shop_chains (id, name) VALUES (1, 'Lidl')")
    c.execute("INSERT INTO stores (id, name, chain_id) VALUES (1, 'LIDL SRBIJA KD', 1)")
    yield c
    c.close()


@allure.epic("Review & Rules")
@allure.feature("Classification")
class TestClassifyByRules:
    def test_miss_returns_none(self, conn):
        result = classify_by_rules(conn, 1, "jabuka")
        assert result is None

    def test_store_specific_rule_hit(self, conn):
        create_or_update_rule(conn, 1, "jabuka", RuleSpec(1, 3, "llm"))
        result = classify_by_rules(conn, 1, "jabuka")
        assert isinstance(result, RuleHit)
        assert result.category_id == 1
        assert result.confidence_level == 3

    def test_generic_rule_hit(self, conn):
        create_or_update_rule(conn, None, "hleb", RuleSpec(1, 4, "user_correction"))
        result = classify_by_rules(conn, 1, "hleb")
        assert isinstance(result, RuleHit)
        assert result.category_id == 1
        assert result.confidence_level == 4

    def test_store_specific_beats_generic(self, conn):
        create_or_update_rule(conn, None, "mleko", RuleSpec(1, 2, "llm"))
        create_or_update_rule(conn, 1, "mleko", RuleSpec(2, 4, "user_correction"))
        result = classify_by_rules(conn, 1, "mleko")
        assert isinstance(result, RuleHit)
        assert result.category_id == 2
        assert result.confidence_level == 4

    def test_generic_rule_applies_to_different_chain(self, conn):
        conn.execute("INSERT INTO shop_chains (id, name) VALUES (2, 'Maxi')")
        create_or_update_rule(conn, None, "sir", RuleSpec(1, 3, "llm"))
        result = classify_by_rules(conn, 2, "sir")
        assert isinstance(result, RuleHit)
        assert result.category_id == 1
        assert result.confidence_level == 3

    def test_chain_rule_shared_across_stores_of_same_chain(self, conn):
        conn.execute("INSERT INTO stores (id, name, chain_id) VALUES (2, 'LIDL NOVI SAD KD', 1)")
        create_or_update_rule(conn, 1, "mleko", RuleSpec(1, 3, "llm"))
        result = classify_by_rules(conn, 1, "mleko")
        assert isinstance(result, RuleHit)
        assert result.category_id == 1

    def test_chain_rule_does_not_apply_to_other_chain(self, conn):
        conn.execute("INSERT INTO shop_chains (id, name) VALUES (2, 'Maxi')")
        create_or_update_rule(conn, 1, "jogurt", RuleSpec(1, 3, "llm"))
        result = classify_by_rules(conn, 2, "jogurt")
        assert result is None

    def test_no_chain_id_miss(self, conn):
        create_or_update_rule(conn, 1, "jogurt", RuleSpec(1, 3, "llm"))
        result = classify_by_rules(conn, None, "jogurt")
        assert result is None

    def test_no_chain_id_generic_hit(self, conn):
        create_or_update_rule(conn, None, "jogurt", RuleSpec(2, 3, "llm"))
        result = classify_by_rules(conn, None, "jogurt")
        assert isinstance(result, RuleHit)
        assert result.category_id == 2
        assert result.confidence_level == 3

    def test_returns_tag_ids_from_rule(self, conn):
        conn.execute(
            "INSERT INTO classification_rules"
            " (chain_id, item_name_normalized, category_id, confidence_level, source, tag_ids)"
            " VALUES (1, 'testitem', 1, 3, 'llm', ?)",
            [json.dumps([5, 7])],
        )
        result = classify_by_rules(conn, 1, "testitem")
        assert isinstance(result, RuleHit)
        assert result.category_id == 1
        assert result.confidence_level == 3
        assert sorted(result.tag_ids) == [5, 7]

    def test_returns_empty_tag_ids_when_empty_json(self, conn):
        conn.execute(
            "INSERT INTO classification_rules"
            " (chain_id, item_name_normalized, category_id, confidence_level, source, tag_ids)"
            " VALUES (1, 'nulltags', 1, 4, 'user_correction', '[]')",
        )
        result = classify_by_rules(conn, 1, "nulltags")
        assert isinstance(result, RuleHit)
        assert result.tag_ids == []


@allure.epic("Review & Rules")
@allure.feature("Classification")
class TestCreateOrUpdateRule:
    def test_insert_new_rule(self, conn):
        create_or_update_rule(conn, 1, "banana", RuleSpec(1, 3, "llm"))
        result = classify_by_rules(conn, 1, "banana")
        assert isinstance(result, RuleHit)
        assert result.category_id == 1
        assert result.confidence_level == 3

    def test_update_existing_rule(self, conn):
        create_or_update_rule(conn, 1, "banana", RuleSpec(1, 3, "llm"))
        create_or_update_rule(conn, 1, "banana", RuleSpec(2, 4, "user_correction"))
        result = classify_by_rules(conn, 1, "banana")
        assert isinstance(result, RuleHit)
        assert result.category_id == 2
        assert result.confidence_level == 4

    def test_user_correction_always_conf4(self, conn):
        create_or_update_rule(conn, 1, "sladoled", RuleSpec(1, 2, "user_correction"))
        result = classify_by_rules(conn, 1, "sladoled")
        assert isinstance(result, RuleHit)
        assert result.confidence_level == 4

    def test_generic_and_store_rules_independent(self, conn):
        create_or_update_rule(conn, None, "voda", RuleSpec(2, 3, "llm"))
        create_or_update_rule(conn, 1, "voda", RuleSpec(1, 4, "user_correction"))
        generic = classify_by_rules(conn, None, "voda")
        store = classify_by_rules(conn, 1, "voda")
        assert (
            isinstance(generic, RuleHit)
            and generic.category_id == 2
            and generic.confidence_level == 3
        )
        assert isinstance(store, RuleHit) and store.category_id == 1 and store.confidence_level == 4

    def test_llm_persists_alternative_category_ids(self, conn):
        create_or_update_rule(
            conn,
            1,
            "kivi",
            RuleSpec(1, 3, "llm", alternative_category_ids=(2,), tag_ids=(10,)),
        )
        row = conn.execute(
            "SELECT alternative_category_ids, tag_ids FROM classification_rules"
            " WHERE item_name_normalized = 'kivi'",
        ).fetchone()
        assert row is not None
        assert json.loads(row[0]) == [2]
        assert json.loads(row[1]) == [10]

    def test_insert_persists_llm_name(self, conn):
        create_or_update_rule(conn, 1, "mango", RuleSpec(1, 3, "llm", llm_name="groq-llama"))
        row = conn.execute(
            "SELECT llm_name FROM classification_rules WHERE item_name_normalized = 'mango'",
        ).fetchone()
        assert row is not None
        assert row[0] == "groq-llama"

    def test_llm_name_defaults_null(self, conn):
        create_or_update_rule(conn, 1, "limun", RuleSpec(1, 3, "user_correction"))
        row = conn.execute(
            "SELECT llm_name FROM classification_rules WHERE item_name_normalized = 'limun'",
        ).fetchone()
        assert row is not None
        assert row[0] is None

    def test_update_overwrites_llm_name(self, conn):
        create_or_update_rule(conn, 1, "breskva", RuleSpec(1, 3, "llm", llm_name="groq-llama"))
        create_or_update_rule(conn, 1, "breskva", RuleSpec(2, 4, "user_correction"))
        row = conn.execute(
            "SELECT llm_name FROM classification_rules WHERE item_name_normalized = 'breskva'",
        ).fetchone()
        assert row is not None
        assert row[0] is None

    def test_user_correction_preserves_spec_alternative_category_ids(self, conn):
        create_or_update_rule(
            conn,
            1,
            "ananas",
            RuleSpec(1, 3, "llm", alternative_category_ids=(2,), tag_ids=(10,)),
        )
        create_or_update_rule(
            conn,
            1,
            "ananas",
            RuleSpec(2, 4, "user_correction", alternative_category_ids=(1,), tag_ids=(11,)),
        )
        row = conn.execute(
            "SELECT alternative_category_ids, tag_ids FROM classification_rules"
            " WHERE item_name_normalized = 'ananas'",
        ).fetchone()
        assert row is not None
        assert json.loads(row[0]) == [1]
        assert json.loads(row[1]) == [11]

    def test_user_correction_overwrites_tag_ids(self, conn):
        create_or_update_rule(
            conn,
            1,
            "smokva",
            RuleSpec(1, 3, "llm", tag_ids=(5,)),
        )
        create_or_update_rule(
            conn,
            1,
            "smokva",
            RuleSpec(1, 4, "user_correction", tag_ids=(6,)),
        )
        row = conn.execute(
            "SELECT tag_ids FROM classification_rules WHERE item_name_normalized = 'smokva'",
        ).fetchone()
        assert row is not None
        assert json.loads(row[0]) == [6]
