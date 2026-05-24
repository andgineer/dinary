"""Tests for SqliteLLMBrokerStorage: provider loading, seeding, call logging, rate limiting."""

import asyncio
import shutil
import sqlite3
import unittest.mock
from datetime import UTC, datetime, timedelta
from pathlib import Path

import allure
import pytest

from dinary.adapters.llm_storage import SqliteLLMBrokerStorage, _label_from_base_url
from dinary.adapters.llmbroker import CallEvent
from dinary.db import db_migrations, storage


def _migration_connect(self, dburi):
    con = sqlite3.connect(str(self.uri.database), isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=5000")
    return con


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    blank = tmp_path / "blank.db"
    with unittest.mock.patch.object(db_migrations.SQLiteBackend, "connect", _migration_connect):
        db_migrations.migrate_db(blank)
    dst = tmp_path / "dinary.db"
    shutil.copy(blank, dst)
    monkeypatch.setattr(storage, "DB_PATH", dst)
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    return dst


def _write_toml(path: Path, providers: list[dict]) -> None:
    lines = []
    for p in providers:
        lines.append("\n[[providers]]")
        for k, v in p.items():
            lines.append(f'{k} = "{v}"')
    path.write_text("\n".join(lines), encoding="utf-8")


def _make_event(
    provider_id: int = 1,
    status: str = "ok",
    latency_ms: int = 100,
    rate_limited_until: datetime | None = None,
    error_detail: str | None = None,
) -> CallEvent:
    return CallEvent(
        provider_id=provider_id,
        context_id=None,
        status=status,
        latency_ms=latency_ms,
        timestamp=datetime.now(UTC),
        rate_limited_until=rate_limited_until,
        error_detail=error_detail,
    )


@allure.epic("Services")
@allure.feature("SqliteLLMBrokerStorage")
class TestLoadProviders:
    def test_returns_enabled_providers_sorted_by_priority(self, fresh_db):
        conn = storage.get_connection()
        try:
            conn.execute(
                "INSERT INTO llmbroker_providers"
                " (label, base_url, api_key, model, priority, is_enabled)"
                " VALUES ('P2', 'https://b', 'k2', 'm', 1, 1)"
            )
            conn.execute(
                "INSERT INTO llmbroker_providers"
                " (label, base_url, api_key, model, priority, is_enabled)"
                " VALUES ('P1', 'https://a', 'k1', 'm', 0, 1)"
            )
            conn.execute(
                "INSERT INTO llmbroker_providers"
                " (label, base_url, api_key, model, priority, is_enabled)"
                " VALUES ('Disabled', 'https://c', 'k3', 'm', 2, 0)"
            )
        finally:
            conn.close()

        providers = asyncio.run(SqliteLLMBrokerStorage().load_providers())

        assert len(providers) == 2
        assert providers[0].label == "P1"
        assert providers[1].label == "P2"

    def test_seeds_from_toml_when_empty(self, fresh_db, tmp_path, monkeypatch, real_llm_seed):  # noqa: ARG002
        toml = tmp_path / "providers.toml"
        _write_toml(
            toml,
            [
                {
                    "label": "Groq",
                    "base_url": "https://api.groq.com/openai/v1",
                    "api_key": "gsk_test",
                    "model": "llama-3.3-70b-versatile",
                    "rate_limit_sec": "90",
                },
                {
                    "label": "OpenRouter",
                    "base_url": "https://openrouter.ai/api/v1",
                    "api_key": "or_test",
                    "model": "gpt-4o",
                    "rate_limit_sec": "30",
                },
            ],
        )

        providers = asyncio.run(SqliteLLMBrokerStorage(providers_toml=toml).load_providers())

        assert len(providers) == 2
        assert providers[0].label == "Groq"
        assert providers[0].rate_limit_sec == 90
        assert providers[1].label == "OpenRouter"
        assert providers[1].rate_limit_sec == 30

        conn = storage.get_connection()
        try:
            count = conn.execute("SELECT COUNT(*) FROM llmbroker_providers").fetchone()[0]
        finally:
            conn.close()
        assert count == 2

    def test_no_seed_when_toml_absent(self, fresh_db, tmp_path, real_llm_seed):  # noqa: ARG002
        missing = tmp_path / "no_such.toml"

        providers = asyncio.run(SqliteLLMBrokerStorage(providers_toml=missing).load_providers())

        assert providers == []

    def test_no_reseed_when_table_already_has_rows(self, fresh_db, tmp_path):
        conn = storage.get_connection()
        try:
            conn.execute(
                "INSERT INTO llmbroker_providers (label, base_url, api_key, model, priority)"
                " VALUES ('Existing', 'https://x', 'k', 'm', 0)"
            )
        finally:
            conn.close()

        toml = tmp_path / "p.toml"
        _write_toml(
            toml, [{"label": "New", "base_url": "https://y", "api_key": "k2", "model": "m"}]
        )

        providers = asyncio.run(SqliteLLMBrokerStorage(providers_toml=toml).load_providers())

        assert len(providers) == 1
        assert providers[0].label == "Existing"

    def test_rate_limited_until_parsed_correctly(self, fresh_db):
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        conn = storage.get_connection()
        try:
            conn.execute(
                "INSERT INTO llmbroker_providers"
                " (label, base_url, api_key, model, priority, rate_limited_until)"
                " VALUES ('P', 'https://a', 'k', 'm', 0, ?)",
                [future],
            )
        finally:
            conn.close()

        providers = asyncio.run(SqliteLLMBrokerStorage().load_providers())

        assert len(providers) == 1
        assert providers[0].rate_limited_until is not None
        assert providers[0].rate_limited_until > datetime.now(UTC)

    def test_example_toml_is_valid(self):
        example = Path(__file__).resolve().parents[2] / ".deploy.example" / "llm_providers.toml"
        assert example.exists(), "missing .deploy.example/llm_providers.toml"
        providers = asyncio.run(SqliteLLMBrokerStorage(providers_toml=example).load_providers())
        # example has placeholder keys so no rows should be seeded (keys are placeholders)
        # just check the TOML parses without error
        assert isinstance(providers, list)


@allure.epic("Services")
@allure.feature("SqliteLLMBrokerStorage")
class TestOnCallLogged:
    def test_inserts_row_into_llmbroker_call_log(self, fresh_db):
        conn = storage.get_connection()
        try:
            conn.execute(
                "INSERT INTO llmbroker_providers (label, base_url, api_key, model, priority)"
                " VALUES ('P', 'https://a', 'k', 'm', 0)"
            )
            provider_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        finally:
            conn.close()

        event = _make_event(provider_id=provider_id, status="ok", latency_ms=250)
        asyncio.run(SqliteLLMBrokerStorage().on_call_logged(event))

        conn = storage.get_connection()
        try:
            row = conn.execute(
                "SELECT provider_id, status, latency_ms FROM llmbroker_call_log"
            ).fetchone()
        finally:
            conn.close()

        assert row is not None
        assert row[0] == provider_id
        assert row[1] == "ok"
        assert row[2] == 250

    def test_writes_error_detail_when_set(self, fresh_db):
        conn = storage.get_connection()
        try:
            conn.execute(
                "INSERT INTO llmbroker_providers (label, base_url, api_key, model, priority)"
                " VALUES ('P', 'https://a', 'k', 'm', 0)"
            )
            provider_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        finally:
            conn.close()

        event = _make_event(
            provider_id=provider_id, status="error", error_detail="401 Unauthorized"
        )
        asyncio.run(SqliteLLMBrokerStorage().on_call_logged(event))

        conn = storage.get_connection()
        try:
            row = conn.execute("SELECT error_detail FROM llmbroker_call_log").fetchone()
        finally:
            conn.close()

        assert row is not None
        assert row[0] == "401 Unauthorized"

    def test_error_detail_null_on_success(self, fresh_db):
        conn = storage.get_connection()
        try:
            conn.execute(
                "INSERT INTO llmbroker_providers (label, base_url, api_key, model, priority)"
                " VALUES ('P', 'https://a', 'k', 'm', 0)"
            )
            provider_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        finally:
            conn.close()

        event = _make_event(provider_id=provider_id, status="ok")
        asyncio.run(SqliteLLMBrokerStorage().on_call_logged(event))

        conn = storage.get_connection()
        try:
            row = conn.execute("SELECT error_detail FROM llmbroker_call_log").fetchone()
        finally:
            conn.close()

        assert row is not None
        assert row[0] is None


@allure.epic("Services")
@allure.feature("SqliteLLMBrokerStorage")
class TestOnRateLimited:
    def test_updates_rate_limited_until_on_provider(self, fresh_db):
        conn = storage.get_connection()
        try:
            conn.execute(
                "INSERT INTO llmbroker_providers (label, base_url, api_key, model, priority)"
                " VALUES ('P', 'https://a', 'k', 'm', 0)"
            )
            provider_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        finally:
            conn.close()

        until = datetime.now(UTC) + timedelta(minutes=5)
        asyncio.run(SqliteLLMBrokerStorage().on_rate_limited(provider_id, until))

        conn = storage.get_connection()
        try:
            row = conn.execute(
                "SELECT rate_limited_until FROM llmbroker_providers WHERE id = ?", [provider_id]
            ).fetchone()
        finally:
            conn.close()

        assert row is not None
        assert row[0] is not None


@allure.epic("Services")
@allure.feature("SqliteLLMBrokerStorage")
class TestLabelFromBaseUrl:
    def test_groq(self):
        assert _label_from_base_url("https://api.groq.com/openai/v1") == "Groq"

    def test_openrouter(self):
        assert _label_from_base_url("https://openrouter.ai/api/v1") == "OpenRouter"

    def test_gemini(self):
        assert _label_from_base_url("https://generativelanguage.googleapis.com/v1beta") == "Gemini"

    def test_fallback_to_hostname(self):
        assert _label_from_base_url("https://myservice.example.com/v1") == "Myservice"
