"""LLM admin API tests — read-only status plus the user disable/enable latch.

Providers are owned by the preset file and mirrored into the broker on startup;
there is no add/edit/delete path. Tests seed a temp preset via
``settings.llm_providers_file`` and let the real lifespan ``sync`` mirror it.
"""

import asyncio
import contextlib
import unittest.mock

import allure
import llmbroker
import pytest
from fastapi.testclient import TestClient

from dinary.adapters import rate_helpers
from dinary.config import settings
from dinary.db import category_seed, db_migrations, storage
from dinary.main import create_app

from _api_helpers import db  # noqa: F401

_TWO_PROVIDERS = """
[[llms]]
name        = "groq-llama"
base_url    = "https://api.groq.com/openai/v1"
model       = "llama-3.3-70b"
api_key_ref = "GROQ_API_KEY"

[[llms]]
name        = "openrouter"
base_url    = "https://openrouter.ai/api/v1"
model       = "gpt-oss-120b"
api_key_ref = "OPENROUTER_API_KEY"

[keys]
GROQ_API_KEY       = "Create a free key at console.groq.com/keys."
OPENROUTER_API_KEY = "Create a free key at openrouter.ai/keys."
"""


@contextlib.contextmanager
def _build_client():
    """Mirrors the shared ``client`` fixture but keeps the network stubs active for
    the whole lifespan (startup, requests, and shutdown)."""
    with (
        unittest.mock.patch.object(rate_helpers, "_get_json_or_none", return_value=None),
        unittest.mock.patch.object(db_migrations, "migrate_db"),
        unittest.mock.patch.object(category_seed, "bootstrap_categories"),
    ):
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c


@pytest.fixture
def seed_providers(tmp_path, monkeypatch):
    """Write a two-provider preset and point the app at it before it starts.

    Only GROQ_API_KEY is present in the environment, so ``groq-llama`` resolves a
    key (status available) while ``openrouter`` does not (status no_key).
    """
    path = tmp_path / "llms.toml"
    path.write_text(_TWO_PROVIDERS)
    monkeypatch.setattr(settings, "llm_providers_file", path)
    monkeypatch.setenv("GROQ_API_KEY", "real-key")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    return path


@pytest.fixture
def client(db):  # noqa: ARG001
    """Empty pool: the default preset path does not exist, so ``sync`` mirrors nothing."""
    with _build_client() as c:
        yield c


@pytest.fixture
def seeded_client(seed_providers, db):  # noqa: ARG001
    """Two-provider pool: ``seed_providers`` sets the preset path before the app builds,
    so the lifespan ``sync`` mirrors it into the broker."""
    with _build_client() as c:
        yield c


@allure.epic("Receipts")
@allure.feature("Admin")
class TestLLMStatus:
    def test_status_empty(self, client):
        resp = client.get("/api/llm/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["providers"] == []
        h = data["health"]
        assert h["healthy"] == 0
        assert h["total"] == 0
        assert h["strategy"] is None

    def test_status_lists_synced_providers(self, seeded_client):
        providers = seeded_client.get("/api/llm/status").json()["providers"]
        assert {p["name"] for p in providers} == {"groq-llama", "openrouter"}

    def test_no_provider_crud_routes(self, client):
        # The add/edit/delete surface is gone entirely.
        assert client.post("/api/llm/providers", json={}).status_code in (404, 405)
        assert client.patch("/api/llm/providers/groq-llama", json={}).status_code in (404, 405)
        assert client.delete("/api/llm/providers/groq-llama").status_code in (404, 405)

    def test_status_provider_fields(self, seeded_client):
        data = seeded_client.get("/api/llm/status").json()
        p = next(x for x in data["providers"] if x["name"] == "groq-llama")
        for field in (
            "name",
            "model",
            "base_url",
            "disabled",
            "has_key",
            "cooldown_until",
            "status",
            "call_count",
            "last_status",
            "last_at",
            "demoted",
            "quality_bound",
            "help",
        ):
            assert field in p
        assert "api_key" not in p
        assert "api_key_ref" not in p

    def test_available_when_key_present(self, seeded_client):
        p = next(
            x
            for x in seeded_client.get("/api/llm/status").json()["providers"]
            if x["name"] == "groq-llama"
        )
        assert p["has_key"] is True
        assert p["status"] == "available"
        assert p["help"] is None

    def test_no_key_status_and_onboarding_hint(self, seeded_client):
        p = next(
            x
            for x in seeded_client.get("/api/llm/status").json()["providers"]
            if x["name"] == "openrouter"
        )
        assert p["has_key"] is False
        assert p["status"] == "no_key"
        assert "openrouter" in p["help"].lower()

    def test_disabled_status_precedes_no_key(self, seeded_client):
        seeded_client.post("/api/llm/providers/openrouter/disable")
        p = next(
            x
            for x in seeded_client.get("/api/llm/status").json()["providers"]
            if x["name"] == "openrouter"
        )
        assert p["disabled"] is True
        assert p["status"] == "disabled"

    def test_health_counts_available(self, seeded_client):
        h = seeded_client.get("/api/llm/status").json()["health"]
        assert h["total"] == 2
        assert h["healthy"] == 1  # only groq-llama has a key
        assert h["strategy"] == "failover"

    def test_quality_bound_null_without_ratings(self, seeded_client):
        p = next(
            x
            for x in seeded_client.get("/api/llm/status").json()["providers"]
            if x["name"] == "groq-llama"
        )
        assert p["quality_bound"] is None
        assert p["demoted"] is False


@allure.epic("Receipts")
@allure.feature("Admin")
class TestLLMDisableEnable:
    def test_disable_then_enable(self, seeded_client):
        assert seeded_client.post("/api/llm/providers/groq-llama/disable").status_code == 204
        p = next(
            x
            for x in seeded_client.get("/api/llm/status").json()["providers"]
            if x["name"] == "groq-llama"
        )
        assert p["disabled"] is True

        assert seeded_client.post("/api/llm/providers/groq-llama/enable").status_code == 204
        p = next(
            x
            for x in seeded_client.get("/api/llm/status").json()["providers"]
            if x["name"] == "groq-llama"
        )
        assert p["disabled"] is False
        assert p["status"] == "available"

    def test_disable_unknown_provider_404(self, seeded_client):
        assert seeded_client.post("/api/llm/providers/ghost/disable").status_code == 404

    def test_enable_unknown_provider_404(self, seeded_client):
        assert seeded_client.post("/api/llm/providers/ghost/enable").status_code == 404


@allure.epic("Receipts")
@allure.feature("Admin")
class TestDisableSurvivesRebuild:
    def test_latch_persists_across_broker_rebuild(self, db, tmp_path):  # noqa: ARG002
        """The user disable is stored by llmbroker and survives a fresh broker."""
        preset = tmp_path / "llms.toml"
        preset.write_text(_TWO_PROVIDERS)
        source = f"sqlite://{storage.DB_PATH}"

        async def _run() -> None:
            broker = llmbroker.AsyncBroker(source, optimize=llmbroker.Optimizer())
            await broker.sync(preset)
            await broker.disable_llm("groq-llama")
            await broker.aclose()

            rebuilt = llmbroker.AsyncBroker(source, optimize=llmbroker.Optimizer())
            snap = await rebuilt.snapshot()
            assert snap["groq-llama"].disabled is True
            assert snap["openrouter"].disabled is False
            await rebuilt.aclose()

        asyncio.run(_run())
