"""LLM admin API tests — read-only status plus the user disable/enable latch.

Providers are owned by llmbroker's curated model list, merged into the DB registry
on startup; there is no add/edit/delete path. Tests write the registry directly and
start the app with the model-list sync switched off, so nothing reaches the network.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import allure
import llmbroker
import pytest
from llmbroker.sqlite import Store

from dinary.api.controllers.llm import RECENT_WINDOW_DAYS
from dinary.config import settings
from dinary.db import storage

from _api_helpers import db  # noqa: F401
from _llmbroker_support import build_app_client, seed_registry

_TWO_PROVIDERS = [
    llmbroker.LLMConfig(
        name="groq-llama",
        base_url="https://api.groq.com/openai/v1",
        model="llama-3.3-70b",
        api_key_ref="GROQ_API_KEY",
    ),
    llmbroker.LLMConfig(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        model="gpt-oss-120b",
        api_key_ref="OPENROUTER_API_KEY",
    ),
]


def _ago(days: float) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


def _call(
    call_id: str,
    *,
    status: llmbroker.CallStatus,
    ts: datetime,
    name: str = "groq-llama",
) -> llmbroker.Call:
    return llmbroker.Call(
        id=call_id,
        llm_name=name,
        operation=None,
        trace_id=None,
        status=status,
        ts=ts,
    )


def _journal(*calls: llmbroker.Call, ratings: tuple[tuple[str, float], ...] = ()) -> None:
    """Seed the call journal through llmbroker's own store — the table schema is not
    a public contract, so no test writes ``llmbroker_*`` directly."""

    async def _run() -> None:
        store = Store(storage.DB_PATH)
        try:
            for call in calls:
                await store.record(call)
            for call_id, score in ratings:
                await store.record_quality(call_id, score)
        finally:
            await store.aclose()

    asyncio.run(_run())


def _provider(client, name: str) -> dict:
    return next(x for x in client.get("/api/llm/status").json()["providers"] if x["name"] == name)


@pytest.fixture
def seed_providers(db, monkeypatch):  # noqa: ARG001
    """Put two providers in the registry before the app starts.

    Only GROQ_API_KEY is stored, so ``groq-llama`` resolves a key (status
    available) while ``openrouter`` does not (status no_key).
    """
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    seed_registry(_TWO_PROVIDERS, keys=[("GROQ_API_KEY", "real-key")])


@pytest.fixture
def client(db):  # noqa: ARG001
    """Empty pool: nothing was mirrored into the registry and no sync fills it."""
    with build_app_client() as c:
        yield c


@pytest.fixture
def seeded_client(seed_providers):  # noqa: ARG001
    """Two-provider pool, seeded into the registry before the app builds."""
    with build_app_client() as c:
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

    def test_schema_mismatch_is_not_reported_as_an_empty_pool(self, client):
        """A database left at another release's store schema is a deployment fault.
        Answering 200 with "no providers" would send the operator to the one screen
        that cannot fix it."""

        class _Mismatched:
            async def snapshot(self):
                raise llmbroker.SchemaVersionError("stale", found=5, expected=7)

        client.app.state.llms = _Mismatched()

        assert client.get("/api/llm/status").status_code == 500

    def test_closed_broker_still_reports_an_empty_pool(self, client):
        """A status read racing lifespan shutdown gets a bare RuntimeError from
        llmbroker; the screen reports an empty pool rather than failing."""

        class _Closed:
            async def snapshot(self):
                raise RuntimeError("the broker is closed")

        client.app.state.llms = _Closed()

        resp = client.get("/api/llm/status")
        assert resp.status_code == 200
        assert resp.json()["providers"] == []

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
            "recent_calls",
            "recent_failures",
            "recent_window_days",
            "demoted",
            "quality_bound",
            "help",
        ):
            assert field in p
        for field in ("call_count", "last_status", "last_at"):
            assert field not in p
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

    def test_no_key_status_without_an_onboarding_hint(self, seeded_client):
        p = next(
            x
            for x in seeded_client.get("/api/llm/status").json()["providers"]
            if x["name"] == "openrouter"
        )
        assert p["has_key"] is False
        assert p["status"] == "no_key"
        # The test app follows no curated list, so llmbroker has no hint for the ref —
        # absent, never an empty string, which the UI would render as a blank hint.
        assert p["help"] is None

    def test_no_key_status_carries_the_curated_hint(self, seed_providers, monkeypatch):  # noqa: ARG002
        """The server's registry lives in the database; the hint still comes from the
        curated list it follows. The sync interval stays None, so nothing is fetched."""
        monkeypatch.setattr(settings, "llm_sync_source", "freetier")
        with build_app_client() as c:
            p = next(
                x for x in c.get("/api/llm/status").json()["providers"] if x["name"] == "openrouter"
            )
        assert p["status"] == "no_key"
        assert p["help"] == llmbroker.curated_pool().keys["OPENROUTER_API_KEY"].help
        assert p["help"]

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
class TestProviderReliability:
    def test_counts_calls_and_failures_in_the_window(self, seeded_client):
        _journal(
            _call("ok-1", status=llmbroker.CallStatus.OK, ts=_ago(1)),
            _call("ok-2", status=llmbroker.CallStatus.OK, ts=_ago(2)),
            _call("err-1", status=llmbroker.CallStatus.ERROR, ts=_ago(3)),
        )
        p = _provider(seeded_client, "groq-llama")
        assert p["recent_calls"] == 3
        assert p["recent_failures"] == 1

    def test_a_rating_moves_neither_count(self, seeded_client):
        """A rating is its own appended row; the aggregate counts call attempts."""
        _journal(
            _call("ok-1", status=llmbroker.CallStatus.OK, ts=_ago(1)),
            ratings=(("ok-1", 1.0),),
        )
        p = _provider(seeded_client, "groq-llama")
        assert p["recent_calls"] == 1
        assert p["recent_failures"] == 0

    def test_rows_older_than_the_window_are_excluded(self, seeded_client):
        _journal(
            _call("inside", status=llmbroker.CallStatus.ERROR, ts=_ago(RECENT_WINDOW_DAYS - 1)),
            _call("outside", status=llmbroker.CallStatus.ERROR, ts=_ago(RECENT_WINDOW_DAYS + 1)),
        )
        p = _provider(seeded_client, "groq-llama")
        assert p["recent_calls"] == 1
        assert p["recent_failures"] == 1

    def test_superseded_counts_as_neither_a_call_nor_a_failure(self, seeded_client):
        _journal(
            _call("ok-1", status=llmbroker.CallStatus.OK, ts=_ago(1)),
            _call("sup-1", status=llmbroker.CallStatus.SUPERSEDED, ts=_ago(1)),
        )
        p = _provider(seeded_client, "groq-llama")
        assert p["recent_calls"] == 1
        assert p["recent_failures"] == 0

    def test_every_non_ok_status_is_a_failure(self, seeded_client):
        _journal(
            _call("ok-1", status=llmbroker.CallStatus.OK, ts=_ago(1)),
            _call("rate-1", status=llmbroker.CallStatus.RATE_LIMITED, ts=_ago(1)),
            _call("unavail-1", status=llmbroker.CallStatus.UNAVAILABLE, ts=_ago(1)),
            _call("err-1", status=llmbroker.CallStatus.ERROR, ts=_ago(1)),
        )
        p = _provider(seeded_client, "groq-llama")
        assert p["recent_calls"] == 4
        assert p["recent_failures"] == 3

    def test_provider_without_rows_reports_zero_calls(self, seeded_client):
        _journal(_call("ok-1", status=llmbroker.CallStatus.OK, ts=_ago(1)))
        p = _provider(seeded_client, "openrouter")
        assert p["recent_calls"] == 0
        assert p["recent_failures"] == 0

    def test_window_days_matches_the_window_used_for_filtering(self, seeded_client):
        _journal(
            _call("inside", status=llmbroker.CallStatus.OK, ts=_ago(RECENT_WINDOW_DAYS - 1)),
            _call("outside", status=llmbroker.CallStatus.OK, ts=_ago(RECENT_WINDOW_DAYS + 1)),
        )
        p = _provider(seeded_client, "groq-llama")
        assert p["recent_window_days"] == RECENT_WINDOW_DAYS
        assert p["recent_calls"] == 1


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
    def test_latch_persists_across_broker_rebuild(self, db):  # noqa: ARG002
        """The user disable is stored by llmbroker and survives a fresh broker."""
        seed_registry(_TWO_PROVIDERS)
        source = f"sqlite://{storage.DB_PATH}"

        def _broker() -> llmbroker.AsyncBroker:
            return llmbroker.AsyncBroker(
                source,
                optimize=llmbroker.Optimizer(),
                sync=None,
                sync_interval=None,
            )

        async def _run() -> None:
            broker = _broker()
            await broker.disable_llm("groq-llama")
            await broker.aclose()

            rebuilt = _broker()
            snap = await rebuilt.snapshot()
            assert snap["groq-llama"].disabled is True
            assert snap["openrouter"].disabled is False
            await rebuilt.aclose()

        asyncio.run(_run())
