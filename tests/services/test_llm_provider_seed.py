"""Tests for LLM provider bootstrap into llm_providers on init_db."""

import shutil
import tomllib
import unittest.mock

import allure
import pytest

from dinary.config import settings
from dinary.db import db_migrations, storage
from dinary.adapters.llm_bootstrap import _providers_from_toml, seed_llm_provider_if_empty


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
    monkeypatch.setattr(storage, "DB_PATH", dst)
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    return dst


def _write_toml(path, providers):
    lines = []
    for p in providers:
        lines.append("\n[[providers]]")
        for k, v in p.items():
            lines.append(f'{k} = "{v}"')
    path.write_text("\n".join(lines), encoding="utf-8")


@allure.epic("Services")
@allure.feature("LLM Provider Seed")
class TestLLMProviderSeed:
    def test_seeds_from_toml(self, fresh_db, tmp_path, monkeypatch):
        toml = tmp_path / "providers.toml"
        _write_toml(
            toml,
            [
                {
                    "label": "Groq Llama",
                    "base_url": "https://api.groq.com/openai/v1",
                    "api_key": "key1",
                    "model": "llama-3.3-70b-versatile",
                },
                {
                    "label": "OpenRouter",
                    "base_url": "https://openrouter.ai/api/v1",
                    "api_key": "key2",
                    "model": "openai/gpt-oss-120b:free",
                },
            ],
        )
        monkeypatch.setattr(settings, "accounting_currency", "EUR")

        conn = storage.get_connection()
        try:
            seed_llm_provider_if_empty(conn, providers_toml=toml)
            rows = conn.execute(
                "SELECT label, base_url, api_key, model, priority"
                " FROM llm_providers ORDER BY priority"
            ).fetchall()
        finally:
            conn.close()

        assert len(rows) == 2
        assert rows[0] == (
            "Groq Llama",
            "https://api.groq.com/openai/v1",
            "key1",
            "llama-3.3-70b-versatile",
            0,
        )
        assert rows[1] == (
            "OpenRouter",
            "https://openrouter.ai/api/v1",
            "key2",
            "openai/gpt-oss-120b:free",
            1,
        )

    def test_toml_label_derived_when_missing(self, fresh_db, tmp_path):
        toml = tmp_path / "providers.toml"
        toml.write_text(
            '[[providers]]\nbase_url = "https://api.groq.com/openai/v1"\napi_key = "k"\nmodel = "m"\n',
            encoding="utf-8",
        )

        conn = storage.get_connection()
        try:
            seed_llm_provider_if_empty(conn, providers_toml=toml)
            label = conn.execute("SELECT label FROM llm_providers").fetchone()[0]
        finally:
            conn.close()
        assert label == "Groq"

    def test_falls_back_to_env_when_toml_absent(self, fresh_db, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "llm_base_url", "https://api.groq.com/openai/v1")
        monkeypatch.setattr(settings, "llm_api_key", "gsk_test_key")
        monkeypatch.setattr(settings, "llm_model", "llama-3.3-70b-versatile")
        monkeypatch.setattr(settings, "accounting_currency", "EUR")
        missing = tmp_path / "no_such_file.toml"

        conn = storage.get_connection()
        try:
            seed_llm_provider_if_empty(conn, providers_toml=missing)
            row = conn.execute(
                "SELECT label, base_url, api_key, model FROM llm_providers"
            ).fetchone()
        finally:
            conn.close()

        assert row is not None
        assert row[0] == "Groq"
        assert row[2] == "gsk_test_key"

    def test_no_seed_when_toml_absent_and_env_empty(self, fresh_db, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "llm_base_url", "")
        monkeypatch.setattr(settings, "llm_api_key", "")
        monkeypatch.setattr(settings, "accounting_currency", "EUR")
        missing = tmp_path / "no_such_file.toml"

        conn = storage.get_connection()
        try:
            seed_llm_provider_if_empty(conn, providers_toml=missing)
            count = conn.execute("SELECT COUNT(*) FROM llm_providers").fetchone()[0]
        finally:
            conn.close()
        assert count == 0

    def test_no_seed_when_already_has_rows(self, fresh_db, tmp_path, monkeypatch):
        toml = tmp_path / "providers.toml"
        _write_toml(
            toml,
            [
                {
                    "label": "P1",
                    "base_url": "https://api.groq.com/openai/v1",
                    "api_key": "key1",
                    "model": "m",
                }
            ],
        )
        monkeypatch.setattr(settings, "accounting_currency", "EUR")

        conn = storage.get_connection()
        try:
            seed_llm_provider_if_empty(conn, providers_toml=toml)
            # Change file content and call again — should NOT insert more rows
            _write_toml(
                toml,
                [
                    {
                        "label": "P2",
                        "base_url": "https://api.groq.com/openai/v1",
                        "api_key": "key2",
                        "model": "m",
                    }
                ],
            )
            seed_llm_provider_if_empty(conn, providers_toml=toml)
            count = conn.execute("SELECT COUNT(*) FROM llm_providers").fetchone()[0]
            api_key = conn.execute("SELECT api_key FROM llm_providers").fetchone()[0]
        finally:
            conn.close()
        assert count == 1
        assert api_key == "key1"

    def test_example_toml_is_valid(self):
        from pathlib import Path

        example = Path(__file__).resolve().parents[2] / ".deploy.example" / "llm_providers.toml"
        assert example.exists(), "missing .deploy.example/llm_providers.toml"
        with example.open("rb") as fh:
            data = tomllib.load(fh)
        assert "providers" in data
        assert len(data["providers"]) >= 1

    def test_providers_from_toml_skips_incomplete_entries(self, tmp_path):
        toml = tmp_path / "p.toml"
        toml.write_text(
            '[[providers]]\nbase_url = "https://api.groq.com/openai/v1"\nmodel = "m"\n'
            '[[providers]]\nbase_url = "https://openrouter.ai/api/v1"\napi_key = "k"\nmodel = "m"\n',
            encoding="utf-8",
        )
        result = _providers_from_toml(toml)
        assert len(result) == 1
        assert result[0]["base_url"] == "https://openrouter.ai/api/v1"
