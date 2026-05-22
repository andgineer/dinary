import asyncio
import shutil
import unittest.mock
from unittest.mock import AsyncMock, patch

import allure
import pytest

from dinary.adapters.llmbroker import LLMBroker, NullStorage
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
        conn.execute("INSERT INTO stores (chain_name, pib) VALUES ('Lidl', '100000001')")
        with patch(
            "dinary.background.classification.store_resolver.get_chain_name",
            new_callable=AsyncMock,
            return_value="ShouldNotBeUsed",
        ) as mock_chain:
            store_id = asyncio.run(resolve_store(conn, _broker(), "100000001", "LIDL SRBIJA KD"))
            mock_chain.assert_not_called()
        row = conn.execute("SELECT chain_name FROM stores WHERE id = ?", [store_id]).fetchone()
        assert row[0] == "Lidl"

    def test_new_pib_new_chain_inserts(self, conn):
        with patch(
            "dinary.background.classification.store_resolver.get_chain_name",
            new_callable=AsyncMock,
            return_value="Maxi",
        ):
            store_id = asyncio.run(resolve_store(conn, _broker(), "200000002", "MAXI DOO"))
        row = conn.execute("SELECT chain_name, pib FROM stores WHERE id = ?", [store_id]).fetchone()
        assert row[0] == "Maxi"
        assert row[1] == "200000002"

    def test_new_pib_known_chain_updates_pib(self, conn):
        conn.execute("INSERT INTO stores (chain_name, pib) VALUES ('DM', NULL)")
        old_id = conn.execute("SELECT id FROM stores WHERE chain_name='DM'").fetchone()[0]
        with patch(
            "dinary.background.classification.store_resolver.get_chain_name",
            new_callable=AsyncMock,
            return_value="DM",
        ):
            store_id = asyncio.run(resolve_store(conn, _broker(), "300000003", "DM DROGERIE MARKT"))
        assert store_id == old_id
        pib = conn.execute("SELECT pib FROM stores WHERE id = ?", [store_id]).fetchone()[0]
        assert pib == "300000003"

    def test_no_pib_still_resolves(self, conn):
        with patch(
            "dinary.background.classification.store_resolver.get_chain_name",
            new_callable=AsyncMock,
            return_value="Roda",
        ):
            store_id = asyncio.run(resolve_store(conn, _broker(), "", "RODA CENTAR"))
        row = conn.execute("SELECT chain_name FROM stores WHERE id = ?", [store_id]).fetchone()
        assert row[0] == "Roda"

    def test_repeat_same_pib_returns_same_store(self, conn):
        with patch(
            "dinary.background.classification.store_resolver.get_chain_name",
            new_callable=AsyncMock,
            return_value="Idea",
        ) as mock_chain:
            id1 = asyncio.run(resolve_store(conn, _broker(), "400000004", "IDEA PLUS"))
            id2 = asyncio.run(resolve_store(conn, _broker(), "400000004", "IDEA PLUS"))
        assert id1 == id2
        mock_chain.assert_called_once()
