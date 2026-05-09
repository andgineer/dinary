"""Tests for DINARY_LLM_* env-var bootstrap into llm_providers on init_db."""

import shutil
import unittest.mock

import allure
import pytest

from dinary.config import settings
from dinary.services import db_migrations, ledger_repo


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    blank = tmp_path / "blank.db"
    import sqlite3

    def _migration_connect(self, dburi):
        con = sqlite3.connect(str(self.uri.database), isolation_level=None)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=5000")
        return con

    with unittest.mock.patch.object(db_migrations.SQLiteBackend, "connect", _migration_connect):
        db_migrations.migrate_db(blank)

    dst = tmp_path / "dinary.db"
    shutil.copy(blank, dst)
    monkeypatch.setattr(ledger_repo, "DB_PATH", dst)
    monkeypatch.setattr(ledger_repo, "DATA_DIR", tmp_path)
    return dst


@allure.epic("Services")
@allure.feature("LLM Provider Seed")
class TestLLMProviderSeed:
    def test_seeds_from_env_vars(self, fresh_db, monkeypatch):
        monkeypatch.setattr(settings, "llm_base_url", "https://api.groq.com/openai/v1")
        monkeypatch.setattr(settings, "llm_api_key", "gsk_test_key")
        monkeypatch.setattr(settings, "llm_model", "llama-3.3-70b-versatile")
        monkeypatch.setattr(settings, "accounting_currency", "EUR")

        ledger_repo.init_db()

        conn = ledger_repo.get_connection()
        try:
            row = conn.execute(
                "SELECT label, base_url, api_key, model FROM llm_providers"
            ).fetchone()
        finally:
            conn.close()

        assert row is not None
        assert row[0] == "Groq"
        assert row[1] == "https://api.groq.com/openai/v1"
        assert row[2] == "gsk_test_key"
        assert row[3] == "llama-3.3-70b-versatile"

    def test_no_seed_when_base_url_empty(self, fresh_db, monkeypatch):
        monkeypatch.setattr(settings, "llm_base_url", "")
        monkeypatch.setattr(settings, "llm_api_key", "key")
        monkeypatch.setattr(settings, "accounting_currency", "EUR")

        ledger_repo.init_db()

        conn = ledger_repo.get_connection()
        try:
            count = conn.execute("SELECT COUNT(*) FROM llm_providers").fetchone()[0]
        finally:
            conn.close()
        assert count == 0

    def test_no_seed_when_already_has_rows(self, fresh_db, monkeypatch):
        monkeypatch.setattr(settings, "llm_base_url", "https://api.groq.com/openai/v1")
        monkeypatch.setattr(settings, "llm_api_key", "key1")
        monkeypatch.setattr(settings, "llm_model", "model-a")
        monkeypatch.setattr(settings, "accounting_currency", "EUR")

        ledger_repo.init_db()

        # Change env vars and call init_db again — should NOT insert a second row
        monkeypatch.setattr(settings, "llm_api_key", "key2")
        monkeypatch.setattr(settings, "llm_model", "model-b")
        ledger_repo.init_db()

        conn = ledger_repo.get_connection()
        try:
            count = conn.execute("SELECT COUNT(*) FROM llm_providers").fetchone()[0]
            api_key = conn.execute("SELECT api_key FROM llm_providers").fetchone()[0]
        finally:
            conn.close()
        assert count == 1
        assert api_key == "key1"

    def test_label_derived_from_url(self, fresh_db, monkeypatch):
        monkeypatch.setattr(settings, "llm_base_url", "https://openrouter.ai/api/v1")
        monkeypatch.setattr(settings, "llm_api_key", "or_key")
        monkeypatch.setattr(settings, "accounting_currency", "EUR")

        ledger_repo.init_db()

        conn = ledger_repo.get_connection()
        try:
            label = conn.execute("SELECT label FROM llm_providers").fetchone()[0]
        finally:
            conn.close()
        assert label == "OpenRouter"
