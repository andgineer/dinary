"""Full pipeline tests: POST /api/receipts → drain → DB outcome.

Invariant under test: if the API returns 200 OK the receipt is never silently
lost — it ends up classified (expenses created + job deleted), poisoned (job in
'poisoned' state, visible in LLMView), or retrying (job pending/sleeping).
"""

import asyncio
import base64
import contextlib
import dataclasses
import sqlite3
import struct
import unittest.mock
from datetime import UTC, datetime
from unittest.mock import patch

import allure
import httpx
import pytest
from fastapi.testclient import TestClient

from unittest.mock import AsyncMock, MagicMock

import llmbroker

from dinary.adapters.rates import helpers
from dinary.adapters.receipts.types import (
    ParsedReceipt,
    ParserNotIndexedError,
    ParserParseError,
    ParserRequestError,
    ReceiptItem,
)
from dinary.background.classification.receipt_classifier import (
    ClassificationResult,
    ClassifyOutcome,
)
from dinary.background.classification.task import _drain_all_pending
from dinary.config import settings
from dinary.db import category_seed, db_migrations, storage
from dinary.main import create_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _broker() -> llmbroker.AsyncBroker:
    # classify_receipt (and resolve_store where used) are patched in these tests, so the
    # broker is only consulted for count(); 1 keeps max_attempts at 1 as before.
    broker = MagicMock(spec=llmbroker.AsyncBroker)
    broker.count = AsyncMock(return_value=1)
    return broker


def _item(name: str, price: float) -> ReceiptItem:
    return ReceiptItem(
        name_raw=name, unit_price=price, quantity=1.0, total_price=price, tax_label="E"
    )


def _parsed(*items: ReceiptItem, fallback: bool = False) -> ParsedReceipt:
    total = sum(i.total_price for i in items)
    return ParsedReceipt(
        store_name="",
        store_pib="",
        total_amount=total,
        invoice_number="INV-PIPE",
        items=list(items),
        items_total=total,
        total_ok=True,
        used_journal_fallback=fallback,
    )


def _result(name: str, cat: int | None, conf: int) -> ClassificationResult:
    return ClassificationResult(item_name_normalized=name, category_id=cat, confidence_level=conf)


def _make_classify_outcome(
    results: list[ClassificationResult],
    broker_unavailable: bool = False,
) -> ClassifyOutcome:
    if broker_unavailable:
        execution = None
    else:
        execution = MagicMock()
        execution.text = "ok"
        execution.llm_name = "test-model"
        execution.record_quality = AsyncMock()
    execution_failed = not broker_unavailable and any(r.category_id is None for r in results)
    return ClassifyOutcome(
        results=results,
        broker_unavailable=broker_unavailable,
        execution_failed=execution_failed,
        execution=execution,
    )


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def pipeline(db, monkeypatch):  # noqa: ARG001
    """TestClient with RSD accounting and catalog seeded."""
    monkeypatch.setattr(settings, "accounting_currency", "RSD")
    with (
        unittest.mock.patch.object(helpers, "_get_json_or_none", return_value=None),
        unittest.mock.patch.object(db_migrations, "migrate_db"),
        unittest.mock.patch.object(category_seed, "bootstrap_categories"),
    ):
        app = create_app()
        with TestClient(app, raise_server_exceptions=True) as client:
            conn = storage.get_connection()
            try:
                conn.execute(
                    "INSERT INTO category_groups (id, name, sort_order, is_active)"
                    " VALUES (1, 'Food', 1, 1)"
                )
                for i in range(1, 7):
                    conn.execute(
                        "INSERT INTO categories (id, name, group_id, is_active)"
                        f" VALUES ({i}, 'cat{i}', 1, 1)"
                    )
            finally:
                conn.close()
            yield client


def _post(client: TestClient, uid: str = "r1") -> int:
    resp = client.post("/api/receipts", json={"client_receipt_id": uid, "url": f"https://x/{uid}"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ok"
    return int(resp.json()["receipt_id"])


def _job_status(receipt_id: int) -> str | None:
    """Return job status string, or None if job row no longer exists."""
    conn = storage.get_connection()
    try:
        row = conn.execute(
            "SELECT status FROM receipt_classification_jobs WHERE receipt_id = ?",
            [receipt_id],
        ).fetchone()
        return str(row[0]) if row else None
    finally:
        conn.close()


def _expense_count(receipt_id: int) -> int:
    conn = storage.get_connection()
    try:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM expenses WHERE receipt_id = ?", [receipt_id]
            ).fetchone()[0]
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@allure.epic("Receipts")
@allure.feature("Full pipeline")
class TestReceiptPipelineNeverLost:
    """Every receipt accepted by the API (200 OK) must reach a visible terminal state."""

    def test_happy_path_creates_expense_and_completes_job(self, pipeline):
        """POST → parse → classify (conf=3) → expense created, job deleted."""
        receipt_id = _post(pipeline)

        with (
            patch(
                "dinary.background.classification.task.parse_receipt",
                return_value=_parsed(_item("HLEB", 100.0)),
            ),
            patch(
                "dinary.background.classification.task.classify_receipt",
                return_value=_make_classify_outcome([_result("hleb", 1, 3)]),
            ),
        ):
            asyncio.run(_drain_all_pending(_broker()))

        assert _expense_count(receipt_id) == 1, "classified item must create an expense"
        assert _job_status(receipt_id) is None, (
            "job must be deleted after successful classification"
        )

    def test_llm_all_none_exhausted_fallback_creates_expense(self, pipeline):
        """LLM returns cat_id=None → exhaustion → fallback creates expense at top category."""
        receipt_id = _post(pipeline)

        with (
            patch(
                "dinary.background.classification.task.parse_receipt",
                return_value=_parsed(_item("Raid Family tec/el.ap.", 849.0)),
            ),
            patch(
                "dinary.background.classification.task.classify_receipt",
                return_value=_make_classify_outcome([_result("raid family tec/el.ap.", None, 1)]),
            ),
        ):
            asyncio.run(_drain_all_pending(_broker()))

        assert _expense_count(receipt_id) == 1, (
            "fallback must create expense at top catalog category"
        )
        assert _job_status(receipt_id) is None, "job must be completed after fallback"

    def test_empty_items_from_parser_poisons_job(self, pipeline):
        """Parser returns a receipt with no items → job poisoned (parse error, not silent drop)."""
        receipt_id = _post(pipeline)

        with patch(
            "dinary.background.classification.task.parse_receipt",
            return_value=_parsed(),  # zero items
        ):
            asyncio.run(_drain_all_pending(_broker()))

        assert _expense_count(receipt_id) == 0
        assert _job_status(receipt_id) == "poisoned", (
            "empty-items receipt must be poisoned, not silently completed"
        )

    def test_parser_transient_error_releases_for_retry(self, pipeline):
        """Network error during receipt fetch → job released as pending, not deleted."""
        receipt_id = _post(pipeline)

        with (
            patch(
                "dinary.background.classification.task.parse_receipt",
                side_effect=ParserRequestError("timeout"),
            ),
            patch("dinary.background.classification.task.notify_new_receipt"),
            patch("dinary.background.classification.task._schedule_wakeup"),
        ):
            asyncio.run(_drain_all_pending(_broker()))

        assert _expense_count(receipt_id) == 0
        assert _job_status(receipt_id) == "pending", (
            "transient parse error must release the job for retry"
        )

    def test_parser_not_indexed_releases_for_retry(self, pipeline):
        """SUF returns no items yet (not indexed) → job released as pending, not poisoned."""
        receipt_id = _post(pipeline)

        with (
            patch(
                "dinary.background.classification.task.parse_receipt",
                side_effect=ParserNotIndexedError("no items yet"),
            ),
            patch("dinary.background.classification.task.notify_new_receipt"),
            patch("dinary.background.classification.task._schedule_wakeup"),
        ):
            asyncio.run(_drain_all_pending(_broker()))

        assert _expense_count(receipt_id) == 0
        assert _job_status(receipt_id) == "pending", (
            "SUF-not-indexed-yet error must release the job for retry, not poison it"
        )

    def test_parser_permanent_error_poisons_job(self, pipeline):
        """Malformed / unsupported receipt format (ParserParseError) → job poisoned."""
        receipt_id = _post(pipeline)

        with patch(
            "dinary.background.classification.task.parse_receipt",
            side_effect=ParserParseError("unrecognised receipt format"),
        ):
            asyncio.run(_drain_all_pending(_broker()))

        assert _expense_count(receipt_id) == 0
        assert _job_status(receipt_id) == "poisoned", "permanent parse error must poison the job"

    def test_poisoned_receipt_resolved_manually(self, pipeline):
        """Poisoned receipt → manual resolve endpoint → expense created, job gone."""
        purchase_dt = datetime(2026, 5, 4, 12, 30, 0, tzinfo=UTC)
        epoch_ms = int(purchase_dt.timestamp() * 1000)
        buf = bytearray(64)
        struct.pack_into("<Q", buf, 25, 1234500)
        struct.pack_into(">Q", buf, 33, epoch_ms)
        vl = base64.b64encode(bytes(buf)).decode()

        resp = pipeline.post(
            "/api/receipts",
            json={"client_receipt_id": "stuck-1", "url": f"https://suf.purs.gov.rs/v/?vl={vl}"},
        )
        assert resp.status_code == 200, resp.text
        receipt_id = int(resp.json()["receipt_id"])

        with patch(
            "dinary.background.classification.task.parse_receipt",
            side_effect=ParserParseError("unrecognised receipt format"),
        ):
            asyncio.run(_drain_all_pending(_broker()))

        assert _job_status(receipt_id) == "poisoned"

        resolve_resp = pipeline.post(
            f"/api/receipts/{receipt_id}/resolve",
            json={"category_id": 1},
        )
        assert resolve_resp.status_code == 204, resolve_resp.text

        _assert_not_lost(receipt_id)
        assert _expense_count(receipt_id) == 1
        assert _job_status(receipt_id) is None

    def test_llm_broker_unavailable_releases_for_retry(self, pipeline):
        """All LLM providers return None → job released for retry, not lost."""
        receipt_id = _post(pipeline)

        with (
            patch(
                "dinary.background.classification.task.parse_receipt",
                return_value=_parsed(_item("MLEKO", 80.0)),
            ),
            patch(
                "dinary.background.classification.task.classify_receipt",
                return_value=_make_classify_outcome(
                    [_result("mleko", None, 1)], broker_unavailable=True
                ),
            ),
        ):
            asyncio.run(_drain_all_pending(_broker()))

        assert _expense_count(receipt_id) == 0
        assert _job_status(receipt_id) == "pending", (
            "LLM broker unavailability must release the job for retry"
        )

    def test_partial_classification_exhausts_to_fallback(self, pipeline):
        """2-item receipt: one cat=None → execution_failed → exhaustion → fallback → 2 expenses."""
        receipt_id = _post(pipeline)

        with (
            patch(
                "dinary.background.classification.task.parse_receipt",
                return_value=_parsed(_item("HLEB", 100.0), _item("NEPOZNATO", 50.0)),
            ),
            patch(
                "dinary.background.classification.task.classify_receipt",
                return_value=_make_classify_outcome(
                    [_result("hleb", 1, 3), _result("nepoznato", None, 1)]
                ),
            ),
        ):
            asyncio.run(_drain_all_pending(_broker()))

        assert _expense_count(receipt_id) == 2, (
            "fallback applies to all llm_queue items → 2 expenses"
        )
        assert _job_status(receipt_id) is None, "job must be completed after fallback"

    def test_duplicate_post_returns_ok_no_extra_job(self, pipeline):
        """Second POST with same client_receipt_id returns 200 duplicate, no second job."""
        receipt_id = _post(pipeline, uid="dup-1")
        resp2 = pipeline.post(
            "/api/receipts",
            json={"client_receipt_id": "dup-1", "url": "https://x/dup-1"},
        )
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "duplicate"
        assert resp2.json()["receipt_id"] == receipt_id

        conn = storage.get_connection()
        try:
            job_count = conn.execute(
                "SELECT COUNT(*) FROM receipt_classification_jobs WHERE receipt_id = ?",
                [receipt_id],
            ).fetchone()[0]
        finally:
            conn.close()

        assert job_count == 1, "duplicate POST must not create a second job"

    def test_already_parsed_retry_skips_parsing(self, pipeline):
        """On retry after parse, parse_receipt is not called — items already in receipt_items."""
        receipt_id = _post(pipeline)

        # First pass: parse completes, classify fails (broker down) → pending
        with (
            patch(
                "dinary.background.classification.task.parse_receipt",
                return_value=_parsed(_item("SIR", 120.0)),
            ),
            patch(
                "dinary.background.classification.task.classify_receipt",
                return_value=_make_classify_outcome(
                    [_result("sir", None, 1)], broker_unavailable=True
                ),
            ),
        ):
            asyncio.run(_drain_all_pending(_broker()))

        assert _job_status(receipt_id) == "pending"

        # Reset retry_after so the job is immediately claimable again
        conn = storage.get_connection()
        try:
            conn.execute(
                "UPDATE receipt_classification_jobs SET retry_after = NULL WHERE receipt_id = ?",
                [receipt_id],
            )
        finally:
            conn.close()

        # Second pass: parse_receipt must NOT be called (receipt already parsed)
        with (
            patch("dinary.background.classification.task.parse_receipt") as mock_parse,
            patch(
                "dinary.background.classification.task.classify_receipt",
                return_value=_make_classify_outcome([_result("sir", 1, 3)]),
            ),
        ):
            asyncio.run(_drain_all_pending(_broker()))
            mock_parse.assert_not_called()

        assert _expense_count(receipt_id) == 1
        assert _job_status(receipt_id) is None

    def test_multi_item_receipt_all_classified(self, pipeline):
        """3-item receipt fully classified → 3 expenses, job completed."""
        receipt_id = _post(pipeline)

        with (
            patch(
                "dinary.background.classification.task.parse_receipt",
                return_value=_parsed(_item("A", 100.0), _item("B", 200.0), _item("C", 50.0)),
            ),
            patch(
                "dinary.background.classification.task.classify_receipt",
                return_value=_make_classify_outcome(
                    [
                        _result("a", 1, 4),
                        _result("b", 1, 3),
                        _result("c", 1, 3),
                    ]
                ),
            ),
        ):
            asyncio.run(_drain_all_pending(_broker()))

        assert _expense_count(receipt_id) == 3
        assert _job_status(receipt_id) is None

    def test_two_receipts_independent_outcomes(self, pipeline):
        """Two concurrent receipts: one classifies (conf=3), one fallbacks → both complete."""
        rid1 = _post(pipeline, uid="two-r1")
        rid2 = _post(pipeline, uid="two-r2")

        def classify_side_effect(broker, normalized_names, *args, **kwargs):
            if normalized_names == ["hleb"]:
                return _make_classify_outcome([_result("hleb", 1, 3)])
            return _make_classify_outcome([_result("raid", None, 1)])

        with (
            patch(
                "dinary.background.classification.task.parse_receipt",
                side_effect=[
                    _parsed(_item("HLEB", 100.0)),
                    _parsed(_item("RAID", 849.0)),
                ],
            ),
            patch(
                "dinary.background.classification.task.classify_receipt",
                side_effect=classify_side_effect,
            ),
        ):
            asyncio.run(_drain_all_pending(_broker()))

        assert _expense_count(rid1) == 1, "first receipt (hleb, conf=3) must create an expense"
        assert _job_status(rid1) is None, "first receipt must be completed"
        assert _expense_count(rid2) == 1, "second receipt (raid, cat=None) → fallback → expense"
        assert _job_status(rid2) is None, "second receipt must be completed after fallback"


# ---------------------------------------------------------------------------
# Chaos invariant infrastructure
# ---------------------------------------------------------------------------

_PARSE = "dinary.background.classification.task.parse_receipt"
_CLASSIFY = "dinary.background.classification.task.classify_receipt"
_RESOLVE = "dinary.background.classification.task.resolve_store"
_PERSIST = "dinary.background.classification.persist.persist_classification_results"

_HLEB = _item("HLEB", 100.0)
_PARSED = _parsed(_HLEB)
_GOOD = _make_classify_outcome([_result("hleb", 1, 3)])


def _parsed_with_store(*items: ReceiptItem) -> ParsedReceipt:
    total = sum(i.total_price for i in items)
    return ParsedReceipt(
        store_name="Lidl",
        store_pib="123456789",
        total_amount=total,
        invoice_number="INV-CHAOS",
        items=list(items),
        items_total=total,
        total_ok=True,
        used_journal_fallback=False,
    )


def _assert_not_lost(receipt_id: int) -> None:
    """Blackbox invariant: a 200-accepted receipt must never silently disappear."""
    if _expense_count(receipt_id) > 0:
        return
    job = _job_status(receipt_id)
    assert job is not None, (
        f"receipt_id={receipt_id}: LOST — job row deleted with zero expenses. "
        "Receipt was accepted by the API (200 OK) but silently vanished."
    )


@dataclasses.dataclass
class _Chaos:
    id: str
    patches: list[tuple[str, dict]]


_SCENARIOS: list[_Chaos] = [
    # parse stage
    _Chaos(
        "parse.network_timeout",
        [(_PARSE, {"side_effect": ParserRequestError("connection timeout")})],
    ),
    _Chaos(
        "parse.http_connect_error",
        [(_PARSE, {"side_effect": httpx.ConnectError("connection refused")})],
    ),
    _Chaos(
        "parse.http_read_timeout", [(_PARSE, {"side_effect": httpx.ReadTimeout("read timeout")})]
    ),
    _Chaos(
        "parse.permanent_format_error",
        [(_PARSE, {"side_effect": ParserParseError("unrecognised receipt format")})],
    ),
    _Chaos(
        "parse.suf_not_indexed_yet",
        [(_PARSE, {"side_effect": ParserNotIndexedError("no items yet")})],
    ),
    _Chaos("parse.os_error", [(_PARSE, {"side_effect": OSError("disk I/O error")})]),
    _Chaos(
        "parse.unexpected_crash",
        [(_PARSE, {"side_effect": RuntimeError("unexpected internal error")})],
    ),
    _Chaos("parse.empty_items", [(_PARSE, {"return_value": _parsed()})]),
    # store resolution
    _Chaos(
        "store.resolve_runtime_error",
        [
            (_PARSE, {"return_value": _parsed_with_store(_HLEB)}),
            (_RESOLVE, {"side_effect": RuntimeError("store DB race")}),
        ],
    ),
    # classify stage
    _Chaos(
        "classify.all_conf1",
        [
            (_PARSE, {"return_value": _PARSED}),
            (_CLASSIFY, {"return_value": _make_classify_outcome([_result("hleb", None, 1)])}),
        ],
    ),
    _Chaos(
        "classify.broker_unavailable",
        [
            (_PARSE, {"return_value": _PARSED}),
            (
                _CLASSIFY,
                {
                    "return_value": _make_classify_outcome(
                        [_result("hleb", None, 1)], broker_unavailable=True
                    )
                },
            ),
        ],
    ),
    _Chaos(
        "classify.zero_results",
        [
            (_PARSE, {"return_value": _PARSED}),
            (_CLASSIFY, {"return_value": _make_classify_outcome([])}),
        ],
    ),
    _Chaos(
        "classify.connection_error",
        [
            (_PARSE, {"return_value": _PARSED}),
            (_CLASSIFY, {"side_effect": ConnectionError("broker unreachable")}),
        ],
    ),
    _Chaos(
        "classify.unexpected_crash",
        [
            (_PARSE, {"return_value": _PARSED}),
            (_CLASSIFY, {"side_effect": RuntimeError("llm internal error")}),
        ],
    ),
    # persist stage
    _Chaos(
        "persist.db_locked",
        [
            (_PARSE, {"return_value": _PARSED}),
            (_CLASSIFY, {"return_value": _GOOD}),
            (_PERSIST, {"side_effect": sqlite3.OperationalError("database is locked")}),
        ],
    ),
    _Chaos(
        "persist.unexpected_crash",
        [
            (_PARSE, {"return_value": _PARSED}),
            (_CLASSIFY, {"return_value": _GOOD}),
            (_PERSIST, {"side_effect": RuntimeError("persist internal error")}),
        ],
    ),
]


@allure.epic("Receipts")
@allure.feature("Full pipeline")
class TestReceiptPipelineInvariant:
    """Parametrized chaos: inject failure at every pipeline stage, assert receipt is never lost."""

    @pytest.mark.parametrize("scenario", _SCENARIOS, ids=lambda s: s.id)
    def test_never_lost_under_chaos(self, pipeline, scenario: _Chaos) -> None:
        receipt_id = _post(pipeline)
        with contextlib.ExitStack() as stack:
            for target, kwargs in scenario.patches:
                stack.enter_context(patch(target, **kwargs))
            asyncio.run(_drain_all_pending(_broker()))
        _assert_not_lost(receipt_id)
