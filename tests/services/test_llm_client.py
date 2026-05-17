import asyncio
import json
import shutil
import sqlite3
import unittest.mock
from unittest.mock import AsyncMock, MagicMock, patch

import allure
import httpx
import pytest

from dinary.services import db_migrations, storage
from dinary.services.llm_client import (
    AllProvidersExhausted,
    OpenAICompatibleClient,
    ProviderPool,
    ReceiptContext,
    _build_user_message,
    _parse_response,
)

_CATEGORIES = {1: "Еда: еда", 2: "Жильё: хозтовары", 3: "Красота и ЗОЖ: гигиена"}


@allure.epic("Services")
@allure.feature("LLM Client")
class TestBuildUserMessage:
    def test_contains_store_name(self):
        msg = _build_user_message(["hleb"], "Lidl", _CATEGORIES)
        assert "Lidl" in msg

    def test_contains_all_items(self):
        msg = _build_user_message(["hleb", "mleko"], "Lidl", _CATEGORIES)
        assert "hleb" in msg
        assert "mleko" in msg

    def test_contains_category_ids_and_names(self):
        msg = _build_user_message(["hleb"], "Lidl", _CATEGORIES)
        assert "1:" in msg
        assert "Еда: еда" in msg


@allure.epic("Services")
@allure.feature("LLM Client")
class TestParseResponse:
    def test_valid_response(self):
        raw = json.dumps(
            [
                {"item": "hleb", "category_id": 1, "confidence": 3},
                {"item": "pasta", "category_id": None, "confidence": 1},
            ]
        )
        results = _parse_response(raw, ["hleb", "pasta"])
        assert len(results) == 2
        assert results[0].category_id == 1
        assert results[0].confidence_level == 3
        assert results[0].item_name_normalized == "hleb"
        assert results[1].category_id is None
        assert results[1].confidence_level == 1

    def test_malformed_json_fallback(self):
        results = _parse_response("not json at all", ["hleb", "mleko"])
        assert len(results) == 2
        assert all(r.confidence_level == 1 for r in results)
        assert all(r.category_id is None for r in results)
        assert results[0].item_name_normalized == "hleb"
        assert results[1].item_name_normalized == "mleko"

    def test_not_list_fallback(self):
        results = _parse_response('{"item": "hleb"}', ["hleb"])
        assert results[0].confidence_level == 1
        assert results[0].category_id is None

    def test_missing_key_fallback(self):
        raw = json.dumps([{"item": "hleb"}])  # missing confidence
        results = _parse_response(raw, ["hleb"])
        assert results[0].confidence_level == 1

    def test_category_id_null_parsed_as_none(self):
        raw = json.dumps([{"item": "hleb", "category_id": None, "confidence": 1}])
        results = _parse_response(raw, ["hleb"])
        assert results[0].category_id is None


@allure.epic("Services")
@allure.feature("LLM Client")
class TestOpenAICompatibleClient:
    def _mock_http(self, response_body: str):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"choices": [{"message": {"content": response_body}}]}
        mock_async_client = AsyncMock()
        mock_async_client.post = AsyncMock(return_value=mock_response)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_async_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        return mock_ctx, mock_async_client

    def test_classify_receipt_success(self):
        response_body = json.dumps(
            [
                {"item": "hleb", "category_id": 1, "confidence": 4},
            ]
        )
        mock_ctx, mock_async_client = self._mock_http(response_body)

        with patch("dinary.services.llm_client.httpx.AsyncClient", return_value=mock_ctx):
            client = OpenAICompatibleClient("https://api.example.com/v1", "key", "model")
            results = asyncio.run(client.classify_receipt(["hleb"], "Lidl", _CATEGORIES))

        assert len(results) == 1
        assert results[0].category_id == 1
        assert results[0].confidence_level == 4
        assert results[0].item_name_normalized == "hleb"

    def test_classify_receipt_sends_correct_model(self):
        response_body = json.dumps([{"item": "hleb", "category_id": 1, "confidence": 3}])
        mock_ctx, mock_async_client = self._mock_http(response_body)

        with patch("dinary.services.llm_client.httpx.AsyncClient", return_value=mock_ctx):
            client = OpenAICompatibleClient("https://api.example.com/v1", "key", "my-model")
            asyncio.run(client.classify_receipt(["hleb"], "Lidl", _CATEGORIES))

        call_kwargs = mock_async_client.post.call_args
        payload = call_kwargs.kwargs["json"]
        assert payload["model"] == "my-model"

    def test_classify_receipt_trailing_slash_stripped(self):
        response_body = json.dumps([{"item": "hleb", "category_id": 1, "confidence": 3}])
        mock_ctx, mock_async_client = self._mock_http(response_body)

        with patch("dinary.services.llm_client.httpx.AsyncClient", return_value=mock_ctx):
            client = OpenAICompatibleClient("https://api.example.com/v1/", "key", "model")
            asyncio.run(client.classify_receipt(["hleb"], "Lidl", _CATEGORIES))

        url = mock_async_client.post.call_args.args[0]
        assert not url.endswith("//chat/completions")
        assert url.endswith("/chat/completions")

    def test_http_error_propagates(self):
        mock_async_client = AsyncMock()
        mock_async_client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "429 Too Many Requests",
                request=MagicMock(),
                response=MagicMock(),
            )
        )
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_async_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("dinary.services.llm_client.httpx.AsyncClient", return_value=mock_ctx):
            client = OpenAICompatibleClient("https://api.example.com/v1", "key", "model")
            with pytest.raises(httpx.HTTPStatusError):
                asyncio.run(client.classify_receipt(["hleb"], "Lidl", _CATEGORIES))

    def test_malformed_llm_response_falls_back(self):
        mock_ctx, _ = self._mock_http("this is not json")

        with patch("dinary.services.llm_client.httpx.AsyncClient", return_value=mock_ctx):
            client = OpenAICompatibleClient("https://api.example.com/v1", "key", "model")
            results = asyncio.run(client.classify_receipt(["hleb"], "Lidl", _CATEGORIES))

        assert len(results) == 1
        assert results[0].confidence_level == 1
        assert results[0].category_id is None


@pytest.fixture
def pool_conn(tmp_path, monkeypatch):
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


def _seed_providers(conn, providers):
    for p in providers:
        conn.execute(
            "INSERT INTO llm_providers (label, base_url, api_key, model, priority, is_enabled)"
            " VALUES (?, ?, ?, ?, ?, 1)",
            [p["label"], p["base_url"], p["api_key"], p["model"], p.get("priority", 0)],
        )


def _ok_http_ctx(response_body: str):
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"choices": [{"message": {"content": response_body}}]}
    mock_async_client = AsyncMock()
    mock_async_client.post = AsyncMock(return_value=mock_response)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_async_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_ctx


def _429_http_ctx():
    resp_429 = MagicMock()
    resp_429.status_code = 429
    resp_429.headers = {}
    mock_async_client = AsyncMock()
    mock_async_client.post = AsyncMock(
        side_effect=httpx.HTTPStatusError("429", request=MagicMock(), response=resp_429)
    )
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_async_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_ctx


_OK_BODY = json.dumps([{"item": "hleb", "category_id": 1, "confidence": 3}])


@allure.epic("Services")
@allure.feature("LLM Client — ProviderPool")
class TestProviderPool:
    def test_no_providers_raises_all_exhausted(self, pool_conn):
        pool = ProviderPool()
        with pytest.raises(AllProvidersExhausted):
            asyncio.run(
                pool.classify_receipt(
                    pool_conn, ["hleb"], "Lidl", _CATEGORIES, ctx=ReceiptContext(receipt_id=1)
                )
            )

    def test_success_on_first_provider(self, pool_conn):
        _seed_providers(
            pool_conn, [{"label": "P1", "base_url": "https://a", "api_key": "k", "model": "m"}]
        )
        with patch(
            "dinary.services.llm_client.httpx.AsyncClient", return_value=_ok_http_ctx(_OK_BODY)
        ):
            results, used_failover = asyncio.run(
                ProviderPool().classify_receipt(pool_conn, ["hleb"], "Lidl", _CATEGORIES)
            )
        assert len(results) == 1
        assert results[0].category_id == 1
        assert not used_failover

    def test_failover_on_429(self, pool_conn):
        _seed_providers(
            pool_conn,
            [
                {
                    "label": "P1",
                    "base_url": "https://a",
                    "api_key": "k1",
                    "model": "m",
                    "priority": 0,
                },
                {
                    "label": "P2",
                    "base_url": "https://b",
                    "api_key": "k2",
                    "model": "m",
                    "priority": 1,
                },
            ],
        )
        call_count = {"n": 0}

        def _side_effect(*_a, **_kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                resp = MagicMock(status_code=429, headers={})
                raise httpx.HTTPStatusError("429", request=MagicMock(), response=resp)
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {"choices": [{"message": {"content": _OK_BODY}}]}
            return mock_resp

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=_side_effect)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("dinary.services.llm_client.httpx.AsyncClient", return_value=mock_ctx):
            results, used_failover = asyncio.run(
                ProviderPool().classify_receipt(pool_conn, ["hleb"], "Lidl", _CATEGORIES)
            )

        assert used_failover
        assert results[0].category_id == 1
        switch = pool_conn.execute(
            "SELECT value FROM app_metadata WHERE key = 'llm_provider_switch_last'"
        ).fetchone()
        assert switch is not None
        assert "P1" in switch[0]

    def test_all_providers_exhausted(self, pool_conn):
        _seed_providers(
            pool_conn,
            [
                {"label": "P1", "base_url": "https://a", "api_key": "k", "model": "m"},
            ],
        )
        with patch("dinary.services.llm_client.httpx.AsyncClient", return_value=_429_http_ctx()):
            with pytest.raises(AllProvidersExhausted):
                asyncio.run(
                    ProviderPool().classify_receipt(pool_conn, ["hleb"], "Lidl", _CATEGORIES)
                )
        exhausted = pool_conn.execute(
            "SELECT value FROM app_metadata WHERE key = 'llm_all_exhausted_last'"
        ).fetchone()
        assert exhausted is not None

    def test_round_robin_index_advances_after_success(self, pool_conn):
        _seed_providers(
            pool_conn,
            [
                {
                    "label": "P1",
                    "base_url": "https://a",
                    "api_key": "k1",
                    "model": "m",
                    "priority": 0,
                },
                {
                    "label": "P2",
                    "base_url": "https://b",
                    "api_key": "k2",
                    "model": "m",
                    "priority": 1,
                },
            ],
        )
        with patch(
            "dinary.services.llm_client.httpx.AsyncClient", return_value=_ok_http_ctx(_OK_BODY)
        ):
            asyncio.run(ProviderPool().classify_receipt(pool_conn, ["hleb"], "Lidl", _CATEGORIES))
        idx = pool_conn.execute(
            "SELECT value FROM app_metadata WHERE key = 'llm_last_provider_idx'"
        ).fetchone()
        assert idx is not None
        assert int(idx[0]) == 1

    def test_switch_metadata_not_cleared_on_failover_success(self, pool_conn):
        _seed_providers(
            pool_conn,
            [
                {
                    "label": "P1",
                    "base_url": "https://a",
                    "api_key": "k1",
                    "model": "m",
                    "priority": 0,
                },
                {
                    "label": "P2",
                    "base_url": "https://b",
                    "api_key": "k2",
                    "model": "m",
                    "priority": 1,
                },
            ],
        )
        pool_conn.execute(
            "INSERT INTO app_metadata (key, value)"
            " VALUES ('llm_provider_switch_last', '2026-05-01 | from: P1 | to: P2')"
        )
        call_count = {"n": 0}

        def _side_effect(*_a, **_kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                resp = MagicMock(status_code=429, headers={})
                raise httpx.HTTPStatusError("429", request=MagicMock(), response=resp)
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {"choices": [{"message": {"content": _OK_BODY}}]}
            return mock_resp

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=_side_effect)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("dinary.services.llm_client.httpx.AsyncClient", return_value=mock_ctx):
            asyncio.run(ProviderPool().classify_receipt(pool_conn, ["hleb"], "Lidl", _CATEGORIES))

        switch = pool_conn.execute(
            "SELECT value FROM app_metadata WHERE key = 'llm_provider_switch_last'"
        ).fetchone()
        assert switch is not None, "switch metadata must persist when failover was used"

    def test_switch_metadata_cleared_on_primary_success(self, pool_conn):
        _seed_providers(
            pool_conn,
            [
                {"label": "P1", "base_url": "https://a", "api_key": "k", "model": "m"},
            ],
        )
        pool_conn.execute(
            "INSERT INTO app_metadata (key, value) VALUES ('llm_provider_switch_last', 'old-event')"
        )
        with patch(
            "dinary.services.llm_client.httpx.AsyncClient", return_value=_ok_http_ctx(_OK_BODY)
        ):
            asyncio.run(ProviderPool().classify_receipt(pool_conn, ["hleb"], "Lidl", _CATEGORIES))
        switch = pool_conn.execute(
            "SELECT value FROM app_metadata WHERE key = 'llm_provider_switch_last'"
        ).fetchone()
        assert switch is None

    def test_pre_rate_limited_provider_sets_used_failover(self, pool_conn):
        """Provider already marked rate_limited_until (from a prior call) skips and sets used_failover."""
        from datetime import UTC, datetime, timedelta

        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        _seed_providers(
            pool_conn,
            [
                {
                    "label": "P1",
                    "base_url": "https://a",
                    "api_key": "k1",
                    "model": "m",
                    "priority": 0,
                },
                {
                    "label": "P2",
                    "base_url": "https://b",
                    "api_key": "k2",
                    "model": "m",
                    "priority": 1,
                },
            ],
        )
        pool_conn.execute(
            "UPDATE llm_providers SET rate_limited_until = ? WHERE label = 'P1'",
            [future],
        )

        with patch(
            "dinary.services.llm_client.httpx.AsyncClient", return_value=_ok_http_ctx(_OK_BODY)
        ):
            results, used_failover = asyncio.run(
                ProviderPool().classify_receipt(pool_conn, ["hleb"], "Lidl", _CATEGORIES)
            )

        assert used_failover, "pre-rate-limited first provider must set used_failover=True"
        assert results[0].category_id == 1

    def test_get_chain_name_marks_rate_limited_on_429(self, pool_conn):
        """get_chain_name marks the provider rate_limited_until when it returns 429."""
        _seed_providers(
            pool_conn, [{"label": "P1", "base_url": "https://a", "api_key": "k", "model": "m"}]
        )
        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.headers = {}
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError("429", request=MagicMock(), response=resp_429)
        )
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("dinary.services.llm_client.httpx.AsyncClient", return_value=mock_ctx):
            result = asyncio.run(ProviderPool().get_chain_name(pool_conn, "LIDL SRBIJA"))

        assert result == "LIDL SRBIJA", "falls back to raw name when all providers fail"
        row = pool_conn.execute(
            "SELECT rate_limited_until FROM llm_providers WHERE label = 'P1'"
        ).fetchone()
        assert row is not None
        assert row[0] is not None, "rate_limited_until must be set after 429"
        switch = pool_conn.execute(
            "SELECT value FROM app_metadata WHERE key = 'llm_provider_switch_last'"
        ).fetchone()
        assert switch is not None, "get_chain_name must record provider switch on 429"
        assert "P1" in switch[0] and "429" in switch[0]

    def test_failover_on_timeout(self, pool_conn):
        """ReadTimeout on provider 1 triggers failover to provider 2."""
        _seed_providers(
            pool_conn,
            [
                {
                    "label": "P1",
                    "base_url": "https://a",
                    "api_key": "k1",
                    "model": "m",
                    "priority": 0,
                },
                {
                    "label": "P2",
                    "base_url": "https://b",
                    "api_key": "k2",
                    "model": "m",
                    "priority": 1,
                },
            ],
        )
        call_count = {"n": 0}

        def _side_effect(*_a, **_kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise httpx.ReadTimeout("timed out")
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {"choices": [{"message": {"content": _OK_BODY}}]}
            return mock_resp

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=_side_effect)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("dinary.services.llm_client.httpx.AsyncClient", return_value=mock_ctx):
            results, used_failover = asyncio.run(
                ProviderPool().classify_receipt(pool_conn, ["hleb"], "Lidl", _CATEGORIES)
            )

        assert used_failover
        assert results[0].category_id == 1
        switch = pool_conn.execute(
            "SELECT value FROM app_metadata WHERE key = 'llm_provider_switch_last'"
        ).fetchone()
        assert switch is not None
        assert "P1" in switch[0]
        assert "ReadTimeout" in switch[0]

    def test_all_exhausted_on_all_timeouts(self, pool_conn):
        """All providers timing out raises AllProvidersExhausted (job stays pending)."""
        _seed_providers(
            pool_conn,
            [{"label": "P1", "base_url": "https://a", "api_key": "k", "model": "m"}],
        )
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ReadTimeout("timed out"))
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("dinary.services.llm_client.httpx.AsyncClient", return_value=mock_ctx):
            with pytest.raises(AllProvidersExhausted):
                asyncio.run(
                    ProviderPool().classify_receipt(pool_conn, ["hleb"], "Lidl", _CATEGORIES)
                )

    def test_get_chain_name_logs_call(self, pool_conn):
        _seed_providers(
            pool_conn, [{"label": "P1", "base_url": "https://a", "api_key": "k", "model": "m"}]
        )
        chain_ctx = MagicMock()
        chain_resp = MagicMock()
        chain_resp.raise_for_status = MagicMock()
        chain_resp.json.return_value = {"choices": [{"message": {"content": "Lidl"}}]}
        chain_client = AsyncMock()
        chain_client.post = AsyncMock(return_value=chain_resp)
        chain_ctx.__aenter__ = AsyncMock(return_value=chain_client)
        chain_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("dinary.services.llm_client.httpx.AsyncClient", return_value=chain_ctx):
            result = asyncio.run(ProviderPool().get_chain_name(pool_conn, "LIDL SRBIJA KD"))

        assert result == "Lidl"
        log_count = pool_conn.execute("SELECT COUNT(*) FROM llm_call_log").fetchone()[0]
        assert log_count == 1
        log_row = pool_conn.execute("SELECT status FROM llm_call_log").fetchone()
        assert log_row[0] == "ok"
