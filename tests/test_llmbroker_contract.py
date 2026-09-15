"""The server's call sites on the real llmbroker, only provider HTTP and curated-list
downloads answered locally — the other suites stub the broker at exactly these points."""

import asyncio
import json
import logging
import time
from datetime import UTC, datetime, timedelta

import allure
import httpx
import llmbroker
import pytest

from dinary.api.controllers.correction_ratings import record_correction_ratings
from dinary.background.classification.receipt_classifier import (
    CLASSIFICATION_OPERATION,
    classify_receipt,
    get_chain_name,
)
from dinary.config import settings
from dinary.db import storage

from _llmbroker_support import (
    FakeProviders,
    build_app_client,
    completion,
    route_provider_http,
    seed_registry,
    serve_bundled_presets,
)

_GROQ = llmbroker.LLMConfig(
    name="groq-llama",
    base_url="https://api.groq.com/openai/v1",
    model="llama-3.3-70b",
    api_key_ref="GROQ_API_KEY",
)
_CATEGORIES = {1: "food", 2: "transit"}


@pytest.fixture
def providers(monkeypatch) -> FakeProviders:
    fake = FakeProviders()
    route_provider_http(monkeypatch, fake)
    serve_bundled_presets(monkeypatch)
    return fake


@pytest.fixture
def one_keyed_provider(db, monkeypatch):  # noqa: ARG001
    for ref in {c.api_key_ref for c in llmbroker.curated_pool().configs} | {"GROQ_API_KEY"}:
        monkeypatch.delenv(ref, raising=False)
    seed_registry([_GROQ], keys=[("GROQ_API_KEY", "fake-groq")])


def _broker() -> llmbroker.AsyncBroker:
    return llmbroker.AsyncBroker(
        f"sqlite://{storage.DB_PATH}",
        optimize=llmbroker.Optimizer(),
        sync=None,
        sync_interval=None,
    )


@allure.epic("Receipts")
@allure.feature("llmbroker contract")
class TestStartup:
    def test_startup_merges_the_curated_list_and_the_status_reports_it(
        self,
        db,  # noqa: ARG002
        monkeypatch,
        providers,  # noqa: ARG002
    ):
        pool = llmbroker.curated_pool()
        for ref in pool.keys:
            monkeypatch.delenv(ref, raising=False)
        keyed_ref = next(iter(pool.keys))
        monkeypatch.setenv(keyed_ref, "fake-key")
        monkeypatch.setattr(settings, "llm_sync_source", "freetier")
        monkeypatch.setattr(settings, "llm_sync_interval_sec", 86400.0)

        with build_app_client() as client:
            status = client.get("/api/llm/status").json()

        by_name = {p["name"]: p for p in status["providers"]}
        assert set(by_name) == {c.name for c in pool.configs}
        for cfg in pool.configs:
            provider = by_name[cfg.name]
            if cfg.api_key_ref == keyed_ref:
                assert provider["status"] == "available"
                assert provider["help"] is None
            else:
                assert provider["status"] == "no_key"
                assert provider["help"] == pool.keys[cfg.api_key_ref].help
        assert status["health"]["total"] == len(pool.configs)


@allure.epic("Receipts")
@allure.feature("llmbroker contract")
class TestClassification:
    def test_a_classification_call_is_journaled_and_rated(
        self,
        one_keyed_provider,  # noqa: ARG002
        providers,
        caplog,
    ):
        providers.reply = lambda _body: completion(
            json.dumps(
                [
                    {"item": "mleko", "category_id": 1, "confidence": 5},
                    {"item": "karta", "category_id": 2, "confidence": 4},
                ],
            ),
        )

        async def run() -> None:
            broker = _broker()
            try:
                assert await broker.count() == 1
                outcome = await classify_receipt(
                    broker,
                    ["mleko", "karta"],
                    "Maxi",
                    _CATEGORIES,
                    execution_id=7,
                )
                assert outcome.broker_unavailable is False
                assert outcome.execution_failed is False
                assert [r.category_id for r in outcome.results] == [1, 2]
                execution = outcome.execution
                assert execution is not None
                assert execution.call_id
                await execution.record_quality(1.0)

                stats = await broker.stats(
                    since=datetime.now(UTC) - timedelta(hours=1),
                    operation=CLASSIFICATION_OPERATION,
                )
                assert stats[_GROQ.name].total == 1

                with caplog.at_level(logging.INFO):
                    await record_correction_ratings(
                        broker,
                        [(execution.call_id, 0.0), ("no-such-call", 0.0)],
                    )
                assert "no rateable call no-such-call" in caplog.text
                assert "record_quality failed" not in caplog.text
            finally:
                await broker.aclose()

        asyncio.run(run())
        assert providers.hosts == ["api.groq.com"]
        assert providers.bodies[0]["model"] == _GROQ.model
        assert providers.bodies[0]["messages"][0]["role"] == "system"

    @pytest.mark.parametrize(
        "failure",
        [
            httpx.Response(401, text="invalid api key"),
            httpx.Response(500, text="upstream down"),
            httpx.Response(429, text="slow down", headers={"retry-after": "60"}),
        ],
    )
    def test_a_pool_that_cannot_answer_within_the_wait_is_broker_unavailable(
        self,
        one_keyed_provider,  # noqa: ARG002
        providers,
        monkeypatch,
        failure,
    ):
        monkeypatch.setattr(settings, "receipt_classification_llm_wait_sec", 0.5)
        providers.reply = lambda _body: failure

        async def run():
            broker = _broker()
            try:
                started = time.monotonic()
                outcome = await classify_receipt(broker, ["mleko"], "Maxi", _CATEGORIES)
                return outcome, time.monotonic() - started
            finally:
                await broker.aclose()

        outcome, elapsed = asyncio.run(run())
        assert outcome.broker_unavailable is True
        assert outcome.execution is None
        assert elapsed < 5


@allure.epic("Receipts")
@allure.feature("llmbroker contract")
class TestChainName:
    def test_the_chain_name_is_the_first_line_of_the_answer(
        self,
        one_keyed_provider,  # noqa: ARG002
        providers,
    ):
        providers.reply = lambda _body: completion("\nMaxi\nextra")

        async def run() -> str:
            broker = _broker()
            try:
                return await get_chain_name(broker, "MAXI 123 BEOGRAD")
            finally:
                await broker.aclose()

        assert asyncio.run(run()) == "Maxi"

    def test_a_failing_pool_falls_back_to_the_raw_store_name(
        self,
        one_keyed_provider,  # noqa: ARG002
        providers,
    ):
        providers.reply = lambda _body: httpx.Response(500, text="upstream down")

        async def run() -> str:
            broker = _broker()
            try:
                return await get_chain_name(broker, "MAXI 123 BEOGRAD")
            finally:
                await broker.aclose()

        assert asyncio.run(run()) == "MAXI 123 BEOGRAD"
