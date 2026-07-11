"""End-to-end receipt pipeline tests.

QR URL → POST /api/receipts → drain (_process_job with mocks)
→ GET /api/rules/feed → PATCH /api/expenses/{id}/category.
"""

import asyncio
from unittest.mock import patch

import allure
import pytest

from unittest.mock import AsyncMock, MagicMock

import llmbroker

from dinary.adapters.receipt_types import ParsedReceipt, ReceiptItem
from dinary.background.classification.receipt_classifier import (
    ClassificationResult,
    ClassifyOutcome,
)
from dinary.background.classification.task import _process_job
from dinary.config import settings
from dinary.db import storage
from dinary.db.receipts import ReceiptJobRow, claim_next_job

from _api_helpers import db  # noqa: F401

_PARSED = ParsedReceipt(
    store_name="Lidl Srbija KD",
    store_pib="100000001",
    total_amount=120.0,
    invoice_number="INV-001",
    items=[
        ReceiptItem(
            name_raw="HLEB BELI",
            unit_price=120.0,
            quantity=1.0,
            total_price=120.0,
            tax_label="E",
        )
    ],
    items_total=120.0,
    total_ok=True,
    used_journal_fallback=False,
)


def _broker() -> llmbroker.AsyncBroker:
    return llmbroker.AsyncBroker(registry=llmbroker.Registry("/nonexistent.toml"))


def _make_classify_outcome(results: list[ClassificationResult]) -> ClassifyOutcome:
    execution = MagicMock()
    execution.text = "ok"
    execution.record_quality = AsyncMock()
    return ClassifyOutcome(
        results=results,
        broker_unavailable=False,
        execution_failed=any(r.category_id is None for r in results),
        execution=execution,
    )


def _run_drain(job, results=None):
    """Run _process_job synchronously with mocked parse and LLM."""
    if results is None:
        results = [
            ClassificationResult(
                item_name_normalized="hleb beli", category_id=1, confidence_level=3
            )
        ]
    with (
        patch(
            "dinary.background.classification.task.parse_receipt",
            return_value=_PARSED,
        ),
        patch(
            "dinary.background.classification.task.classify_receipt",
            return_value=_make_classify_outcome(results),
        ),
        patch(
            "dinary.background.classification.store_resolver.get_chain_name",
            return_value="Lidl",
        ),
    ):
        asyncio.run(_process_job(job, _broker()))


@allure.epic("Receipts")
@allure.feature("Pipeline")
class TestReceiptPipelineE2E:
    @pytest.fixture(autouse=True)
    def _no_fx_conversion(self, monkeypatch):
        monkeypatch.setattr(settings, "accounting_currency", "RSD")

    def test_qr_url_to_expense_created(self, client, db):  # noqa: ARG002
        """POST receipt URL → drain → expense row exists in DB."""
        resp = client.post(
            "/api/receipts",
            json={"client_receipt_id": "e2e-r1", "url": "https://suf.purs.gov.rs/v/?vl=test"},
        )
        assert resp.status_code == 200
        receipt_id = resp.json()["receipt_id"]

        conn = storage.get_connection()
        try:
            job = claim_next_job(conn)
        finally:
            conn.close()
        assert job is not None
        assert job.receipt_id == receipt_id

        _run_drain(job)

        conn = storage.get_connection()
        try:
            exp = conn.execute(
                "SELECT id, amount, category_id, confidence_level"
                " FROM expenses WHERE receipt_id = ?",
                [receipt_id],
            ).fetchone()
        finally:
            conn.close()

        assert exp is not None
        assert exp[1] == 120.0
        assert exp[2] == 1
        assert exp[3] == 3

    def test_expense_visible_in_review_feed(self, client, db):  # noqa: ARG002
        """After drain, the doubtful expense rule appears in the review feed."""
        client.post(
            "/api/receipts",
            json={"client_receipt_id": "e2e-r2", "url": "https://suf.purs.gov.rs/v/?vl=abc"},
        )
        conn = storage.get_connection()
        try:
            job = claim_next_job(conn)
        finally:
            conn.close()
        _run_drain(job)

        resp = client.get("/api/rules/feed")
        assert resp.status_code == 200
        data = resp.json()
        doubtful = [i for i in data["items"] if i["is_doubtful"]]
        assert len(doubtful) >= 1
        assert doubtful[0]["name"] == "hleb beli"

    def test_category_correction_updates_expense(self, client, db):  # noqa: ARG002
        """PATCH /api/expenses/{id}/category sets conf=4 and creates a rule."""
        resp = client.post(
            "/api/receipts",
            json={"client_receipt_id": "e2e-r3", "url": "https://suf.purs.gov.rs/v/?vl=def"},
        )
        receipt_id = resp.json()["receipt_id"]

        conn = storage.get_connection()
        try:
            job = claim_next_job(conn)
        finally:
            conn.close()
        _run_drain(job)

        conn = storage.get_connection()
        try:
            exp = conn.execute(
                "SELECT id FROM expenses WHERE receipt_id = ?", [receipt_id]
            ).fetchone()
        finally:
            conn.close()
        expense_id = exp[0]

        patch_resp = client.patch(f"/api/expenses/{expense_id}/category", json={"category_id": 2})
        assert patch_resp.status_code == 200
        assert patch_resp.json()["corrected_expense_id"] == expense_id

        conn = storage.get_connection()
        try:
            updated = conn.execute(
                "SELECT category_id, confidence_level FROM expenses WHERE id = ?",
                [expense_id],
            ).fetchone()
            rule = conn.execute(
                "SELECT category_id FROM classification_rules"
                " WHERE item_name_normalized = 'hleb beli'"
            ).fetchone()
        finally:
            conn.close()

        assert updated[0] == 2
        assert updated[1] == 4
        assert rule is not None
        assert rule[0] == 2

    def test_drain_job_deleted_on_completion(self, client, db):  # noqa: ARG002
        """The receipt_classification_jobs row is deleted after a successful drain."""
        resp = client.post(
            "/api/receipts",
            json={"client_receipt_id": "e2e-r4", "url": "https://suf.purs.gov.rs/v/?vl=ghi"},
        )
        receipt_id = resp.json()["receipt_id"]

        conn = storage.get_connection()
        try:
            job = claim_next_job(conn)
        finally:
            conn.close()
        _run_drain(job)

        conn = storage.get_connection()
        try:
            remaining = conn.execute(
                "SELECT status FROM receipt_classification_jobs WHERE receipt_id = ?",
                [receipt_id],
            ).fetchone()
        finally:
            conn.close()

        assert remaining is None

    def test_idempotent_post_receipt(self, client, db):  # noqa: ARG002
        """Duplicate POST with same client_receipt_id returns 'duplicate' without double-queuing."""
        body = {"client_receipt_id": "e2e-idem", "url": "https://suf.purs.gov.rs/v/?vl=xyz"}
        r1 = client.post("/api/receipts", json=body)
        r2 = client.post("/api/receipts", json=body)

        assert r1.json()["status"] == "ok"
        assert r2.json()["status"] == "duplicate"
        assert r2.json()["receipt_id"] == r1.json()["receipt_id"]

        conn = storage.get_connection()
        try:
            job_count = conn.execute("SELECT COUNT(*) FROM receipt_classification_jobs").fetchone()[
                0
            ]
        finally:
            conn.close()
        assert job_count == 1

    def test_drain_idempotent_on_stale_job(self, client, db):  # noqa: ARG002
        """Re-running drain on a receipt that already has expenses is a safe no-op."""

        resp = client.post(
            "/api/receipts",
            json={"client_receipt_id": "e2e-stale", "url": "https://suf.purs.gov.rs/v/?vl=stu"},
        )
        receipt_id = resp.json()["receipt_id"]

        conn = storage.get_connection()
        try:
            job = claim_next_job(conn)
        finally:
            conn.close()

        # First drain — classify_receipt should be called once
        with (
            patch(
                "dinary.background.classification.task.classify_receipt",
                return_value=_make_classify_outcome(
                    [ClassificationResult("hleb beli", category_id=1, confidence_level=3)]
                ),
            ) as mock_classify,
            patch(
                "dinary.background.classification.task.parse_receipt",
                return_value=_PARSED,
            ),
            patch(
                "dinary.background.classification.store_resolver.get_chain_name",
                return_value="Lidl",
            ),
        ):
            asyncio.run(_process_job(job, _broker()))
        assert mock_classify.call_count == 1

        # Simulate stale re-claim — expenses must not be duplicated
        stale_job = ReceiptJobRow(
            receipt_id=receipt_id,
            url="https://suf.purs.gov.rs/v/?vl=stu",
            store_name_raw="",
            store_pib_raw="",
            invoice_number="",
            parsed_at="now",
            used_journal_fallback=False,
            claim_token="stale-token",
        )
        with patch(
            "dinary.background.classification.task.classify_receipt",
            return_value=(
                [ClassificationResult("hleb beli", category_id=1, confidence_level=3)],
                False,
            ),
        ):
            asyncio.run(_process_job(stale_job, _broker()))

        conn = storage.get_connection()
        try:
            exp_count = conn.execute(
                "SELECT COUNT(*) FROM expenses WHERE receipt_id = ?", [receipt_id]
            ).fetchone()[0]
        finally:
            conn.close()
        assert exp_count == 1

    def test_n_items_create_n_expenses(self, client, db):  # noqa: ARG002
        """3-item receipt → 3 individual expense rows."""
        _parsed_3 = ParsedReceipt(
            store_name="Lidl Srbija KD",
            store_pib="100000001",
            total_amount=300.0,
            invoice_number="INV-3ITEMS",
            items=[
                ReceiptItem(
                    name_raw="HLEB",
                    unit_price=100.0,
                    quantity=1.0,
                    total_price=100.0,
                    tax_label="E",
                ),
                ReceiptItem(
                    name_raw="MLEKO",
                    unit_price=120.0,
                    quantity=1.0,
                    total_price=120.0,
                    tax_label="E",
                ),
                ReceiptItem(
                    name_raw="SIR",
                    unit_price=80.0,
                    quantity=1.0,
                    total_price=80.0,
                    tax_label="E",
                ),
            ],
            items_total=300.0,
            total_ok=True,
            used_journal_fallback=False,
        )
        results_3 = [
            ClassificationResult("hleb", category_id=1, confidence_level=3),
            ClassificationResult("mleko", category_id=1, confidence_level=3),
            ClassificationResult("sir", category_id=1, confidence_level=3),
        ]

        resp = client.post(
            "/api/receipts",
            json={"client_receipt_id": "e2e-3items", "url": "https://suf.purs.gov.rs/v/?vl=3items"},
        )
        assert resp.status_code == 200
        receipt_id = resp.json()["receipt_id"]

        conn = storage.get_connection()
        try:
            job = claim_next_job(conn)
        finally:
            conn.close()

        with (
            patch(
                "dinary.background.classification.task.parse_receipt",
                return_value=_parsed_3,
            ),
            patch(
                "dinary.background.classification.task.classify_receipt",
                return_value=_make_classify_outcome(results_3),
            ),
            patch(
                "dinary.background.classification.store_resolver.get_chain_name",
                return_value="Lidl",
            ),
        ):
            asyncio.run(_process_job(job, _broker()))

        conn = storage.get_connection()
        try:
            exp_count = conn.execute(
                "SELECT COUNT(*) FROM expenses WHERE receipt_id = ?", [receipt_id]
            ).fetchone()[0]
        finally:
            conn.close()

        assert exp_count == 3, f"expected 3 expenses for 3-item receipt, got {exp_count}"
