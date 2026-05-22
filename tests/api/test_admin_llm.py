"""LLM admin API tests."""

import allure

from _api_helpers import db  # noqa: F401


def _add_provider(
    client,
    label="Groq",
    base_url="https://api.groq.com/openai/v1",
    api_key="key",
    model="llama-3.3-70b",
    priority=0,
):
    return client.post(
        "/api/llm/providers",
        json={
            "label": label,
            "base_url": base_url,
            "api_key": api_key,
            "model": model,
            "priority": priority,
        },
    )


@allure.epic("API")
@allure.feature("LLM Admin")
class TestLLMProvidersCRUD:
    def test_list_empty(self, client, db):  # noqa: ARG002
        resp = client.get("/api/llm/providers")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_add_provider(self, client, db):  # noqa: ARG002
        resp = _add_provider(client)
        assert resp.status_code == 201
        assert "id" in resp.json()

    def test_list_shows_added(self, client, db):  # noqa: ARG002
        _add_provider(client, label="Groq")
        providers = client.get("/api/llm/providers").json()
        assert len(providers) == 1
        assert providers[0]["label"] == "Groq"

    def test_patch_label(self, client, db):  # noqa: ARG002
        pid = _add_provider(client).json()["id"]
        resp = client.patch(f"/api/llm/providers/{pid}", json={"label": "Groq Updated"})
        assert resp.status_code == 200
        updated = client.get("/api/llm/providers").json()[0]
        assert updated["label"] == "Groq Updated"

    def test_patch_model(self, client, db):  # noqa: ARG002
        pid = _add_provider(client).json()["id"]
        client.patch(f"/api/llm/providers/{pid}", json={"model": "new-model"})
        updated = client.get("/api/llm/providers").json()[0]
        assert updated["model"] == "new-model"

    def test_delete_provider(self, client, db):  # noqa: ARG002
        _add_provider(client, label="P1")
        _add_provider(client, label="P2")
        providers = client.get("/api/llm/providers").json()
        pid = providers[0]["id"]
        resp = client.delete(f"/api/llm/providers/{pid}")
        assert resp.status_code == 200
        remaining = client.get("/api/llm/providers").json()
        assert len(remaining) == 1

    def test_delete_only_enabled_provider_refused(self, client, db):  # noqa: ARG002
        pid = _add_provider(client).json()["id"]
        resp = client.delete(f"/api/llm/providers/{pid}")
        assert resp.status_code == 409

    def test_delete_nonexistent(self, client, db):  # noqa: ARG002
        resp = client.delete("/api/llm/providers/9999")
        assert resp.status_code == 404

    def test_patch_nonexistent(self, client, db):  # noqa: ARG002
        resp = client.patch("/api/llm/providers/9999", json={"label": "x"})
        assert resp.status_code == 404


@allure.epic("API")
@allure.feature("LLM Admin")
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
        assert "priority" in p
        assert "used_today" in p
        assert "last_status" in p
        assert "api_key" not in p

    def test_health_single_provider(self, client, db):  # noqa: ARG002
        _add_provider(client)
        data = client.get("/api/llm/status").json()
        h = data["health"]
        assert h["total"] == 1
        assert h["healthy"] == 1
        assert h["strategy"] is None

    def test_health_two_providers_strategy_failover(self, client, db):  # noqa: ARG002
        _add_provider(client, label="P1")
        _add_provider(client, label="P2")
        data = client.get("/api/llm/status").json()
        h = data["health"]
        assert h["total"] == 2
        assert h["strategy"] == "failover"

    def test_status_includes_pending_receipts_zero_when_empty(self, client, db):  # noqa: ARG002
        data = client.get("/api/llm/status").json()
        assert "pending_receipts" in data
        assert data["pending_receipts"] == 0

    def test_status_pending_receipts_counts_pending_and_in_progress_jobs(
        self,
        client,
        db,  # noqa: ARG002
    ):
        from dinary.db import storage

        conn = storage.get_connection()
        try:
            conn.execute(
                "INSERT INTO receipts (id, client_receipt_id, url) VALUES (1, 'r1', 'https://x')"
            )
            conn.execute(
                "INSERT INTO receipts (id, client_receipt_id, url) VALUES (2, 'r2', 'https://y')"
            )
            conn.execute(
                "INSERT INTO receipt_classification_jobs (receipt_id, status) VALUES (1, 'pending')"
            )
            conn.execute(
                "INSERT INTO receipt_classification_jobs (receipt_id, status)"
                " VALUES (2, 'in_progress')"
            )
        finally:
            conn.close()

        data = client.get("/api/llm/status").json()
        assert data["pending_receipts"] == 2
