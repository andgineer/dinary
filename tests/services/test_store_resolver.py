import asyncio
import shutil
import unittest.mock
from unittest.mock import AsyncMock, patch

import allure
import pytest

from conftest import NullStorage
from dinary.adapters.llmbroker import LLMBroker
from dinary.background.classification.store_resolver import resolve_store
from dinary.db import db_migrations, storage


@pytest.fixture
def conn(tmp_path, monkeypatch):
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
    yield c
    c.close()


def _broker() -> LLMBroker:
    return LLMBroker(NullStorage())


@allure.epic("Services")
@allure.feature("Store Resolver")
class TestResolveStore:
    def test_pib_cache_hit_no_llm(self, conn):
        conn.execute("INSERT INTO shop_chains (name) VALUES ('Lidl')")
        chain_id = conn.execute("SELECT id FROM shop_chains WHERE name='Lidl'").fetchone()[0]
        conn.execute(
            "INSERT INTO stores (name, chain_id, pib) VALUES ('LIDL SRBIJA KD', ?, '100000001')",
            [chain_id],
        )
        with patch(
            "dinary.background.classification.store_resolver.get_chain_name",
            new_callable=AsyncMock,
        ) as mock_chain:
            store_id, got_chain_id = asyncio.run(
                resolve_store(_broker(), "100000001", "LIDL SRBIJA KD")
            )
            mock_chain.assert_not_called()
        row = conn.execute("SELECT name FROM stores WHERE id = ?", [store_id]).fetchone()
        assert row[0] == "LIDL SRBIJA KD"
        assert got_chain_id == chain_id

    def test_new_pib_new_chain_inserts(self, conn):
        with patch(
            "dinary.background.classification.store_resolver.get_chain_name",
            new_callable=AsyncMock,
            return_value="Maxi",
        ):
            store_id, chain_id = asyncio.run(
                resolve_store(_broker(), "200000002", "MAXI DOO BEOGRAD")
            )
        row = conn.execute(
            "SELECT s.name, s.pib, sc.name FROM stores s"
            " JOIN shop_chains sc ON sc.id = s.chain_id WHERE s.id = ?",
            [store_id],
        ).fetchone()
        assert row[0] == "MAXI DOO BEOGRAD"
        assert row[1] == "200000002"
        assert row[2] == "Maxi"
        assert (
            chain_id == conn.execute("SELECT id FROM shop_chains WHERE name='Maxi'").fetchone()[0]
        )

    def test_same_chain_multiple_stores(self, conn):
        """Two stores with different PIBs share one shop_chains row and the same chain_id."""
        with patch(
            "dinary.background.classification.store_resolver.get_chain_name",
            new_callable=AsyncMock,
            return_value="Lidl",
        ):
            store_id1, chain_id1 = asyncio.run(
                resolve_store(_broker(), "300000001", "LIDL SRBIJA KD")
            )
            store_id2, chain_id2 = asyncio.run(
                resolve_store(_broker(), "300000002", "LIDL SRBIJA KD")
            )
        assert store_id1 != store_id2
        assert chain_id1 == chain_id2, "both stores must share the same shop_chains row"

    def test_no_pib_cache_hit_no_llm(self, conn):
        conn.execute("INSERT INTO shop_chains (name) VALUES ('Roda')")
        chain_id = conn.execute("SELECT id FROM shop_chains WHERE name='Roda'").fetchone()[0]
        conn.execute("INSERT INTO stores (name, chain_id) VALUES ('RODA CENTAR', ?)", [chain_id])
        with patch(
            "dinary.background.classification.store_resolver.get_chain_name",
            new_callable=AsyncMock,
        ) as mock_chain:
            store_id, got_chain_id = asyncio.run(resolve_store(_broker(), "", "RODA CENTAR"))
            mock_chain.assert_not_called()
        row = conn.execute("SELECT name FROM stores WHERE id = ?", [store_id]).fetchone()
        assert row[0] == "RODA CENTAR"
        assert got_chain_id == chain_id

    def test_no_pib_inserts_and_resolves(self, conn):
        with patch(
            "dinary.background.classification.store_resolver.get_chain_name",
            new_callable=AsyncMock,
            return_value="Idea",
        ):
            store_id, chain_id = asyncio.run(resolve_store(_broker(), "", "IDEA PLUS BEOGRAD"))
        row = conn.execute(
            "SELECT s.name, s.pib, sc.name FROM stores s"
            " JOIN shop_chains sc ON sc.id = s.chain_id WHERE s.id = ?",
            [store_id],
        ).fetchone()
        assert row[0] == "IDEA PLUS BEOGRAD"
        assert row[1] is None
        assert row[2] == "Idea"
        assert (
            chain_id == conn.execute("SELECT id FROM shop_chains WHERE name='Idea'").fetchone()[0]
        )

    def test_repeat_same_pib_returns_same_store(self, conn):
        with patch(
            "dinary.background.classification.store_resolver.get_chain_name",
            new_callable=AsyncMock,
            return_value="Idea",
        ) as mock_chain:
            id1, _ = asyncio.run(resolve_store(_broker(), "400000004", "IDEA PLUS"))
            id2, _ = asyncio.run(resolve_store(_broker(), "400000004", "IDEA PLUS"))
        assert id1 == id2
        mock_chain.assert_called_once()

    def test_concurrent_pib_conflict_returns_existing_store(self, conn):
        """When a concurrent task inserts the store during the LLM await, the fallback
        SELECT by pib must find the winner instead of crashing."""

        async def competing_insert(broker, store_name_raw):
            conn.execute("INSERT INTO shop_chains (name) VALUES ('Winner') ON CONFLICT DO NOTHING")
            chain_id = conn.execute("SELECT id FROM shop_chains WHERE name='Winner'").fetchone()[0]
            conn.execute(
                "INSERT INTO stores (name, chain_id, pib) VALUES ('WINNER STORE', ?, '555000001')",
                [chain_id],
            )
            return "Loser"

        with patch(
            "dinary.background.classification.store_resolver.get_chain_name",
            side_effect=competing_insert,
        ):
            store_id, _ = asyncio.run(resolve_store(_broker(), "555000001", "WINNER STORE"))

        row = conn.execute("SELECT name FROM stores WHERE id = ?", [store_id]).fetchone()
        assert row[0] == "WINNER STORE"

    def test_no_connection_held_during_llm_call(self, conn):
        """resolve_store must not hold an open connection while awaiting the LLM."""
        held_during_llm: list[bool] = []

        async def fake_chain_name(broker, store_name_raw):
            c = storage.get_connection()
            try:
                c.execute("SELECT 1")
                held_during_llm.append(False)
            finally:
                c.close()
            return "TestChain"

        with patch(
            "dinary.background.classification.store_resolver.get_chain_name",
            side_effect=fake_chain_name,
        ):
            asyncio.run(resolve_store(_broker(), "", "TEST STORE"))

        assert held_during_llm == [False], (
            "a second connection must be openable during the LLM call"
        )
