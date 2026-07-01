"""LLM admin API tests — broker-only (no raw SQL over llmbroker_* tables)."""

import allure

from _api_helpers import db  # noqa: F401


def _add_provider(
    client,
    name="groq-llama",
    base_url="https://api.groq.com/openai/v1",
    api_key_ref="GROQ_API_KEY",
    model="llama-3.3-70b",
):
    return client.post(
        "/api/llm/providers",
        json={
            "name": name,
            "base_url": base_url,
            "api_key_ref": api_key_ref,
            "model": model,
        },
    )


@allure.epic("Receipts")
@allure.feature("Admin")
class TestLLMProvidersCRUD:
    def test_list_empty(self, client, db):  # noqa: ARG002
        resp = client.get("/api/llm/status")
        assert resp.status_code == 200
        assert resp.json()["providers"] == []

    def test_add_provider(self, client, db):  # noqa: ARG002
        resp = _add_provider(client)
        assert resp.status_code == 204
        providers = client.get("/api/llm/status").json()["providers"]
        assert providers[0]["name"] == "groq-llama"

    def test_list_shows_added(self, client, db):  # noqa: ARG002
        _add_provider(client, name="groq-llama")
        providers = client.get("/api/llm/status").json()["providers"]
        assert len(providers) == 1
        assert providers[0]["name"] == "groq-llama"

    def test_patch_model(self, client, db):  # noqa: ARG002
        _add_provider(client, name="groq-llama")
        resp = client.patch("/api/llm/providers/groq-llama", json={"model": "new-model"})
        assert resp.status_code == 200
        updated = client.get("/api/llm/status").json()["providers"][0]
        assert updated["model"] == "new-model"

    def test_patch_base_url(self, client, db):  # noqa: ARG002
        _add_provider(client, name="groq-llama")
        client.patch("/api/llm/providers/groq-llama", json={"base_url": "https://x/v1"})
        updated = client.get("/api/llm/status").json()["providers"][0]
        assert updated["base_url"] == "https://x/v1"

    def test_delete_provider(self, client, db):  # noqa: ARG002
        _add_provider(client, name="p1")
        _add_provider(client, name="p2")
        resp = client.delete("/api/llm/providers/p1")
        assert resp.status_code == 204
        remaining = client.get("/api/llm/status").json()["providers"]
        assert len(remaining) == 1

    def test_delete_only_provider_refused(self, client, db):  # noqa: ARG002
        _add_provider(client, name="groq-llama")
        resp = client.delete("/api/llm/providers/groq-llama")
        assert resp.status_code == 409

    def test_add_duplicate_rejected(self, client, db):  # noqa: ARG002
        assert _add_provider(client, name="groq-llama").status_code == 204
        resp = _add_provider(client, name="groq-llama")
        assert resp.status_code == 409

    def test_delete_nonexistent(self, client, db):  # noqa: ARG002
        resp = client.delete("/api/llm/providers/ghost")
        assert resp.status_code == 404

    def test_patch_nonexistent(self, client, db):  # noqa: ARG002
        resp = client.patch("/api/llm/providers/ghost", json={"model": "x"})
        assert resp.status_code == 404


@allure.epic("Receipts")
@allure.feature("Admin")
class TestLLMStatus:
    def test_status_empty(self, client, db):  # noqa: ARG002
        resp = client.get("/api/llm/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "providers" in data
        assert "health" in data
        h = data["health"]
        assert "healthy" in h
        assert "total" in h
        assert "strategy" in h

    def test_status_shows_providers(self, client, db):  # noqa: ARG002
        _add_provider(client)
        resp = client.get("/api/llm/status")
        assert len(resp.json()["providers"]) == 1

    def test_status_provider_fields(self, client, db):  # noqa: ARG002
        _add_provider(client)
        data = client.get("/api/llm/status").json()
        p = data["providers"][0]
        assert "base_url" in p
        assert "used_today" in p
        assert "last_status" in p
        assert "execution_fail_count" in p
        assert "api_key" not in p

    def test_health_single_provider(self, client, db):  # noqa: ARG002
        _add_provider(client)
        data = client.get("/api/llm/status").json()
        h = data["health"]
        assert h["total"] == 1
        assert h["healthy"] == 1
        assert h["strategy"] is None

    def test_health_two_providers_strategy_failover(self, client, db):  # noqa: ARG002
        _add_provider(client, name="p1")
        _add_provider(client, name="p2")
        data = client.get("/api/llm/status").json()
        h = data["health"]
        assert h["total"] == 2
        assert h["strategy"] == "failover"
