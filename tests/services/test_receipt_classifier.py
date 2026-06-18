import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import allure

from dinary.adapters.llmbroker import Execution, LLMBroker
from dinary.background.classification.receipt_classifier import (
    ClassifyOutcome,
    _build_user_message,
    _parse_response,
    classify_receipt,
    get_chain_name,
)

_CATEGORIES = {1: "Food: food", 2: "Housing: household-goods", 3: "Beauty: hygiene"}


def _make_broker(raw_content: str | None) -> LLMBroker:
    broker = MagicMock(spec=LLMBroker)
    storage_mock = MagicMock()
    storage_mock.on_quality_feedback = AsyncMock()
    execution = Execution(
        output=raw_content,
        provider_label="P1" if raw_content is not None else None,
        storage=storage_mock,
    )
    broker.execute = AsyncMock(return_value=execution)
    return broker


@allure.epic("Receipts")
@allure.feature("Pipeline")
@allure.story("LLM client")
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
        assert "Food: food" in msg

    def test_tags_block_included_when_tags_provided(self):
        msg = _build_user_message(["hleb"], "Lidl", _CATEGORIES, {1: "dog"})
        assert "Tags:" in msg
        assert "dog" in msg

    def test_tags_block_omitted_when_empty(self):
        msg = _build_user_message(["hleb"], "Lidl", _CATEGORIES, {})
        assert "Tags:" not in msg


@allure.epic("Receipts")
@allure.feature("Pipeline")
@allure.story("LLM client")
class TestParseResponse:
    def test_valid_response(self):
        raw = json.dumps(
            [
                {"item": "hleb", "category_id": 1, "confidence": 3},
                {"item": "pasta", "category_id": None, "confidence": 1},
            ]
        )
        results = _parse_response(raw, set())
        assert len(results) == 2
        assert results[0].category_id == 1
        assert results[0].confidence_level == 3
        assert results[0].item_name_normalized == "hleb"
        assert results[1].category_id is None
        assert results[1].confidence_level == 1

    def test_malformed_json_raises_value_error(self):
        import pytest

        with pytest.raises((ValueError, json.JSONDecodeError)):
            _parse_response("not json at all", set())

    def test_not_list_raises_value_error(self):
        import pytest

        with pytest.raises(ValueError, match="expected list"):
            _parse_response('{"item": "hleb"}', set())

    def test_missing_category_id_raises(self):
        raw = json.dumps([{"item": "hleb"}])
        with pytest.raises((ValueError, KeyError)):
            _parse_response(raw, set())

    def test_category_id_null_parsed_as_none(self):
        raw = json.dumps([{"item": "hleb", "category_id": None, "confidence": 1}])
        results = _parse_response(raw, set())
        assert results[0].category_id is None

    def test_extracts_alternatives_caps_at_3(self):
        raw = json.dumps(
            [{"item": "x", "category_id": 1, "confidence": 3, "alternatives": [2, 3, 4, 5, 6]}]
        )
        results = _parse_response(raw, set())
        assert results[0].alternative_category_ids == [2, 3, 4]

    def test_alternatives_ignores_non_int(self):
        raw = json.dumps(
            [{"item": "x", "category_id": 1, "confidence": 3, "alternatives": [1, "bad", 2.5, 3]}]
        )
        results = _parse_response(raw, set())
        assert results[0].alternative_category_ids == [1, 3]

    def test_alternatives_missing_key(self):
        raw = json.dumps([{"item": "x", "category_id": 1, "confidence": 3}])
        results = _parse_response(raw, set())
        assert results[0].alternative_category_ids == []

    def test_tags_filtered_to_provided_set(self):
        raw = json.dumps([{"item": "x", "category_id": 1, "confidence": 3, "tags": [1, 2, 5]}])
        results = _parse_response(raw, {1, 2, 3})
        assert sorted(results[0].tag_ids) == [1, 2]

    def test_tags_ignores_non_int(self):
        raw = json.dumps([{"item": "x", "category_id": 1, "confidence": 3, "tags": [1, "x", 2]}])
        results = _parse_response(raw, {1, 2})
        assert sorted(results[0].tag_ids) == [1, 2]

    def test_tags_missing_key(self):
        raw = json.dumps([{"item": "x", "category_id": 1, "confidence": 3}])
        results = _parse_response(raw, {1, 2})
        assert results[0].tag_ids == []

    def test_tags_non_integer_float_excluded(self):
        raw = json.dumps([{"item": "x", "category_id": 1, "confidence": 3, "tags": [1.5]}])
        results = _parse_response(raw, {1, 2})
        assert results[0].tag_ids == []


@allure.epic("Receipts")
@allure.feature("Pipeline")
@allure.story("LLM client")
class TestClassifyReceiptAdapter:
    def test_returns_parsed_results_no_failure_on_first_success(self):
        raw = json.dumps([{"item": "hleb", "category_id": 1, "confidence": 3}])
        broker = _make_broker(raw)

        outcome = asyncio.run(classify_receipt(broker, ["hleb"], "Lidl", _CATEGORIES))

        assert isinstance(outcome, ClassifyOutcome)
        assert len(outcome.results) == 1
        assert outcome.results[0].category_id == 1
        assert outcome.execution_failed is False
        assert outcome.broker_unavailable is False

    def test_passes_execution_id_to_broker(self):
        raw = json.dumps([{"item": "hleb", "category_id": 1, "confidence": 3}])
        broker = _make_broker(raw)

        asyncio.run(classify_receipt(broker, ["hleb"], "Lidl", _CATEGORIES, execution_id=42))

        broker.execute.assert_awaited_once()
        _, kwargs = broker.execute.call_args
        assert kwargs.get("execution_id") == "42"

    def test_broker_unavailable_when_output_is_none(self):
        broker = _make_broker(None)

        outcome = asyncio.run(classify_receipt(broker, ["hleb"], "Lidl", _CATEGORIES))

        assert outcome.broker_unavailable is True
        assert outcome.execution_failed is False
        assert outcome.results == []

    def test_execution_failed_on_parse_error(self):
        broker = _make_broker("not valid json")

        outcome = asyncio.run(classify_receipt(broker, ["hleb"], "Lidl", _CATEGORIES))

        assert outcome.execution_failed is True
        assert outcome.broker_unavailable is False

    def test_execution_failed_on_any_none_category_id(self):
        raw = json.dumps(
            [
                {"item": "hleb", "category_id": 1, "confidence": 3},
                {"item": "nepoznato", "category_id": None, "confidence": 1},
            ]
        )
        broker = _make_broker(raw)

        outcome = asyncio.run(classify_receipt(broker, ["hleb", "nepoznato"], "Lidl", _CATEGORIES))

        assert outcome.execution_failed is True

    def test_execution_failed_on_count_mismatch(self):
        raw = json.dumps([{"item": "hleb", "category_id": 1, "confidence": 3}])
        broker = _make_broker(raw)

        outcome = asyncio.run(classify_receipt(broker, ["hleb", "mleko"], "Lidl", _CATEGORIES))

        assert outcome.execution_failed is True

    def test_no_execution_failed_when_all_ids_present(self):
        raw = json.dumps(
            [
                {"item": "hleb", "category_id": 1, "confidence": 3},
                {"item": "mleko", "category_id": 2, "confidence": 2},
            ]
        )
        broker = _make_broker(raw)

        outcome = asyncio.run(classify_receipt(broker, ["hleb", "mleko"], "Lidl", _CATEGORIES))

        assert outcome.execution_failed is False
        assert len(outcome.results) == 2


@allure.epic("Receipts")
@allure.feature("Pipeline")
@allure.story("LLM client")
class TestGetChainNameAdapter:
    def test_returns_first_non_empty_line(self):
        broker = _make_broker("Lidl")

        result = asyncio.run(get_chain_name(broker, "LIDL SRBIJA KD"))

        assert result == "Lidl"

    def test_returns_store_name_raw_when_broker_returns_none(self):
        broker = _make_broker(None)

        result = asyncio.run(get_chain_name(broker, "UNKNOWN STORE"))

        assert result == "UNKNOWN STORE"

    def test_passes_chain_name_prompt_to_execute_no_wait(self):
        broker = _make_broker("Maxi")

        asyncio.run(get_chain_name(broker, "MAXI AD"))

        _, kwargs = broker.execute.call_args
        assert kwargs.get("wait") is False
        messages = broker.execute.call_args.args[0]
        assert any("MAXI AD" in str(m) for m in messages)
