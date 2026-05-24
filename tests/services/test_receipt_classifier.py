import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import allure

from dinary.adapters.llmbroker import LLMBroker
from dinary.background.classification.receipt_classifier import (
    _build_user_message,
    _parse_response,
    classify_receipt,
    get_chain_name,
)

_CATEGORIES = {1: "Еда: еда", 2: "Жильё: хозтовары", 3: "Красота и ЗОЖ: гигиена"}


@allure.epic("Services")
@allure.feature("LLM Client")
class TestBuildUserMessage:
    def test_contains_store_name(self):
        msg = _build_user_message(["hleb"], "Lidl", _CATEGORIES, {})
        assert "Lidl" in msg

    def test_contains_all_items(self):
        msg = _build_user_message(["hleb", "mleko"], "Lidl", _CATEGORIES, {})
        assert "hleb" in msg
        assert "mleko" in msg

    def test_contains_category_ids_and_names(self):
        msg = _build_user_message(["hleb"], "Lidl", _CATEGORIES, {})
        assert "1:" in msg
        assert "Еда: еда" in msg

    def test_tags_block_included_when_tags_provided(self):
        msg = _build_user_message(["hleb"], "Lidl", _CATEGORIES, {1: "собака"})
        assert "Tags:" in msg
        assert "собака" in msg

    def test_tags_block_omitted_when_empty(self):
        msg = _build_user_message(["hleb"], "Lidl", _CATEGORIES, {})
        assert "Tags:" not in msg


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
        results = _parse_response(raw, ["hleb", "pasta"], set())
        assert len(results) == 2
        assert results[0].category_id == 1
        assert results[0].confidence_level == 3
        assert results[0].item_name_normalized == "hleb"
        assert results[1].category_id is None
        assert results[1].confidence_level == 1

    def test_malformed_json_fallback(self):
        results = _parse_response("not json at all", ["hleb", "mleko"], set())
        assert len(results) == 2
        assert all(r.confidence_level == 1 for r in results)
        assert all(r.category_id is None for r in results)
        assert results[0].item_name_normalized == "hleb"
        assert results[1].item_name_normalized == "mleko"

    def test_not_list_fallback(self):
        results = _parse_response('{"item": "hleb"}', ["hleb"], set())
        assert results[0].confidence_level == 1
        assert results[0].category_id is None

    def test_missing_key_fallback(self):
        raw = json.dumps([{"item": "hleb"}])  # missing confidence
        results = _parse_response(raw, ["hleb"], set())
        assert results[0].confidence_level == 1

    def test_category_id_null_parsed_as_none(self):
        raw = json.dumps([{"item": "hleb", "category_id": None, "confidence": 1}])
        results = _parse_response(raw, ["hleb"], set())
        assert results[0].category_id is None

    def test_extracts_alternatives_caps_at_3(self):
        raw = json.dumps(
            [{"item": "x", "category_id": 1, "confidence": 3, "alternatives": [2, 3, 4, 5, 6]}]
        )
        results = _parse_response(raw, ["x"], set())
        assert results[0].alternative_category_ids == [2, 3, 4]

    def test_alternatives_ignores_non_int(self):
        raw = json.dumps(
            [{"item": "x", "category_id": 1, "confidence": 3, "alternatives": [1, "bad", 2.5, 3]}]
        )
        results = _parse_response(raw, ["x"], set())
        assert results[0].alternative_category_ids == [1, 3]

    def test_alternatives_missing_key(self):
        raw = json.dumps([{"item": "x", "category_id": 1, "confidence": 3}])
        results = _parse_response(raw, ["x"], set())
        assert results[0].alternative_category_ids == []

    def test_tags_filtered_to_provided_set(self):
        raw = json.dumps([{"item": "x", "category_id": 1, "confidence": 3, "tags": [1, 2, 5]}])
        results = _parse_response(raw, ["x"], {1, 2, 3})
        assert sorted(results[0].tag_ids) == [1, 2]

    def test_tags_ignores_non_int(self):
        raw = json.dumps([{"item": "x", "category_id": 1, "confidence": 3, "tags": [1, "x", 2]}])
        results = _parse_response(raw, ["x"], {1, 2})
        assert sorted(results[0].tag_ids) == [1, 2]

    def test_tags_missing_key(self):
        raw = json.dumps([{"item": "x", "category_id": 1, "confidence": 3}])
        results = _parse_response(raw, ["x"], {1, 2})
        assert results[0].tag_ids == []


@allure.epic("Services")
@allure.feature("LLM Client — adapter functions")
class TestClassifyReceiptAdapter:
    def _make_broker(self, raw_content: str) -> LLMBroker:
        broker = MagicMock(spec=LLMBroker)
        broker.chat = AsyncMock(return_value=raw_content)
        return broker

    def test_returns_parsed_results_no_fallback_on_first_success(self):
        raw = json.dumps([{"item": "hleb", "category_id": 1, "confidence": 3}])
        broker = self._make_broker(raw)

        results, used_fallback = asyncio.run(
            classify_receipt(broker, ["hleb"], "Lidl", _CATEGORIES)
        )

        assert len(results) == 1
        assert results[0].category_id == 1
        assert used_fallback is False

    def test_passes_context_id_to_broker(self):
        raw = json.dumps([{"item": "hleb", "category_id": 1, "confidence": 3}])
        broker = self._make_broker(raw)

        asyncio.run(classify_receipt(broker, ["hleb"], "Lidl", _CATEGORIES, context_id=42))

        broker.chat.assert_awaited_once()
        _, kwargs = broker.chat.call_args
        assert kwargs.get("context_id") == "42"

    def test_used_fallback_true_when_broker_returns_none(self):
        broker = MagicMock(spec=LLMBroker)
        broker.chat = AsyncMock(return_value=None)

        results, used_fallback = asyncio.run(
            classify_receipt(broker, ["hleb"], "Lidl", _CATEGORIES)
        )

        assert used_fallback is True
        assert all(r.confidence_level == 1 for r in results)


@allure.epic("Services")
@allure.feature("LLM Client — adapter functions")
class TestGetChainNameAdapter:
    def test_returns_first_non_empty_line(self):
        broker = MagicMock(spec=LLMBroker)
        broker.chat = AsyncMock(return_value="Lidl")

        result = asyncio.run(get_chain_name(broker, "LIDL SRBIJA KD"))

        assert result == "Lidl"

    def test_returns_store_name_raw_when_broker_returns_none(self):
        broker = MagicMock(spec=LLMBroker)
        broker.chat = AsyncMock(return_value=None)

        result = asyncio.run(get_chain_name(broker, "UNKNOWN STORE"))

        assert result == "UNKNOWN STORE"

    def test_passes_chain_name_prompt_to_chat_no_wait(self):
        broker = MagicMock(spec=LLMBroker)
        broker.chat = AsyncMock(return_value="Maxi")

        asyncio.run(get_chain_name(broker, "MAXI AD"))

        _, kwargs = broker.chat.call_args
        assert kwargs.get("wait") is False
        messages = broker.chat.call_args.args[0]
        assert any("MAXI AD" in str(m) for m in messages)
