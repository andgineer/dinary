"""GET /api/receipts/queue and POST /api/receipts/:id/resolve — manual escape hatch
for receipts stuck in the classification queue.
"""

import base64
import struct
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch

import allure

from dinary.db import storage

from _api_helpers import _mock_get_rate, db  # noqa: F401  (autouse + helper)


def _build_vl(amount_units: int, epoch_ms: int) -> str:
    """Mirror webapp/tests/composable-receipt.test.js buildVlPayload.

    bytes 25..32: amount (uint64 little-endian, in 1/10000 units)
    bytes 33..40: milliseconds since epoch (big-endian uint64)
    """
    buf = bytearray(64)
    struct.pack_into("<Q", buf, 25, amount_units)
    struct.pack_into(">Q", buf, 33, epoch_ms)
    return base64.b64encode(bytes(buf)).decode()


_PURCHASE_DT = datetime(2026, 5, 4, 12, 30, 0, tzinfo=UTC)
_VL = _build_vl(1234500, int(_PURCHASE_DT.timestamp() * 1000))
_RECEIPT_URL = f"https://suf.purs.gov.rs/v/?vl={_VL}"

# Montenegrin verify URL: params after the `#/verify` fragment, EUR total in `prc`.
_MNE_RECEIPT_URL = (
    "https://mapr.tax.gov.me/ic/#/verify?iic=0D7C3EE1EEBAB4A08F4D5003FAE35E7B"
    "&tin=03257746&crtd=2026-07-11T15:51:04+02:00&ord=27585&prc=59.10"
)


def _insert_receipt(
    *,
    client_receipt_id="rcid-1",
    url=_RECEIPT_URL,
    store_name_raw="",
    purchase_datetime=None,
    created_at=None,
    store_id=None,
):
    con = storage.get_connection()
    try:
        con.execute(
            "INSERT INTO receipts"
            " (client_receipt_id, url, store_name_raw, purchase_datetime,"
            "  store_id, created_at)"
            " VALUES (?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))",
            [client_receipt_id, url, store_name_raw, purchase_datetime, store_id, created_at],
        )
        return int(con.execute("SELECT last_insert_rowid()").fetchone()[0])
    finally:
        con.close()


def _insert_job(rid, status="pending", retry_count=0, last_error=None):
    con = storage.get_connection()
    try:
        con.execute(
            "INSERT INTO receipt_classification_jobs"
            " (receipt_id, status, retry_count, last_error)"
            " VALUES (?, ?, ?, ?)",
            [rid, status, retry_count, last_error],
        )
    finally:
        con.close()


@allure.epic("Receipts")
@allure.feature("API")
@allure.story("Stuck-receipt queue")
class TestReceiptQueue:
    def test_empty_queue(self, client, db):  # noqa: ARG002
        resp = client.get("/api/receipts/queue")
        assert resp.status_code == 200
        assert resp.json() == {"items": [], "has_more": False}

    def test_lists_pending_receipt_with_decoded_amount(self, client, db):  # noqa: ARG002
        rid = _insert_receipt(store_name_raw="Maxi")
        _insert_job(rid, status="poisoned", retry_count=4, last_error="boom")

        resp = client.get("/api/receipts/queue")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        item = items[0]
        assert item["receipt_id"] == rid
        assert item["status"] == "poisoned"
        assert item["retry_count"] == 4
        assert item["last_error"] == "boom"
        assert item["store_name_raw"] == "Maxi"
        assert Decimal(str(item["amount"])) == Decimal("123.45")
        assert item["currency"] == "RSD"
        assert item["purchase_date"] == _PURCHASE_DT.isoformat()

    def test_lists_montenegrin_receipt_with_eur_currency(self, client, db):  # noqa: ARG002
        rid = _insert_receipt(
            url=_MNE_RECEIPT_URL,
            client_receipt_id="rcid-mne",
            store_name_raw="Blue Marlin",
        )
        _insert_job(rid, status="poisoned", last_error="boom")

        resp = client.get("/api/receipts/queue")
        item = resp.json()["items"][0]
        assert Decimal(str(item["amount"])) == Decimal("59.10")
        assert item["currency"] == "EUR"
        assert item["purchase_date"] == "2026-07-11T15:51:04+02:00"

    def test_item_with_undecodable_url_has_null_amount(self, client, db):  # noqa: ARG002
        rid = _insert_receipt(
            url="https://suf.purs.gov.rs/v/",
            client_receipt_id="rcid-no-vl",
            created_at="2026-06-01 10:00:00",
        )
        _insert_job(rid, status="pending")

        resp = client.get("/api/receipts/queue")
        item = resp.json()["items"][0]
        assert item["amount"] is None
        assert item["currency"] is None
        assert item["purchase_date"] is None

    def test_excludes_recent_pending_job(self, client, db):  # noqa: ARG002
        """A receipt that just entered the queue gets a 5-minute grace period."""
        rid = _insert_receipt(client_receipt_id="rcid-recent")
        _insert_job(rid, status="pending")

        resp = client.get("/api/receipts/queue")
        assert resp.json() == {"items": [], "has_more": False}

    def test_excludes_recent_in_progress_job(self, client, db):  # noqa: ARG002
        rid = _insert_receipt(client_receipt_id="rcid-recent-ip")
        _insert_job(rid, status="in_progress")

        resp = client.get("/api/receipts/queue")
        assert resp.json() == {"items": [], "has_more": False}

    def test_includes_recent_poisoned_job(self, client, db):  # noqa: ARG002
        """Poisoned jobs skip the grace period — no further automatic retries."""
        rid = _insert_receipt(client_receipt_id="rcid-recent-poisoned")
        _insert_job(rid, status="poisoned", last_error="boom")

        resp = client.get("/api/receipts/queue")
        ids = [item["receipt_id"] for item in resp.json()["items"]]
        assert ids == [rid]

    def test_includes_pending_job_after_grace_period(self, client, db):  # noqa: ARG002
        rid = _insert_receipt(
            client_receipt_id="rcid-old-pending",
            created_at="2026-06-01 10:00:00",
        )
        _insert_job(rid, status="pending")

        resp = client.get("/api/receipts/queue")
        ids = [item["receipt_id"] for item in resp.json()["items"]]
        assert ids == [rid]

    def test_orders_oldest_first(self, client, db):  # noqa: ARG002
        newer = _insert_receipt(
            client_receipt_id="rcid-newer",
            created_at="2026-06-02 10:00:00",
        )
        older = _insert_receipt(
            client_receipt_id="rcid-older",
            created_at="2026-06-01 10:00:00",
        )
        _insert_job(newer)
        _insert_job(older)

        resp = client.get("/api/receipts/queue")
        ids = [item["receipt_id"] for item in resp.json()["items"]]
        assert ids == [older, newer]

    def test_pagination(self, client, db):  # noqa: ARG002
        for i in range(3):
            rid = _insert_receipt(
                client_receipt_id=f"rcid-{i}",
                created_at=f"2026-06-0{i + 1} 10:00:00",
            )
            _insert_job(rid)

        page1 = client.get("/api/receipts/queue?page=1&page_size=2").json()
        assert len(page1["items"]) == 2
        assert page1["has_more"] is True

        page2 = client.get("/api/receipts/queue?page=2&page_size=2").json()
        assert len(page2["items"]) == 1
        assert page2["has_more"] is False


@allure.epic("Receipts")
@allure.feature("API")
@allure.story("Manual receipt resolution")
class TestResolveReceipt:
    @patch("dinary.adapters.rates.service.get_rate", side_effect=_mock_get_rate)
    def test_resolve_creates_expense(self, _mock_rate, client, db):  # noqa: ARG002
        rid = _insert_receipt()
        _insert_job(rid, status="poisoned", retry_count=4, last_error="boom")

        resp = client.post(f"/api/receipts/{rid}/resolve", json={"category_id": 1})
        assert resp.status_code == 204, resp.text

        con = storage.get_connection()
        try:
            exp = con.execute(
                "SELECT id, category_id, confidence_level, rule_id, amount_original,"
                "       currency_original, receipt_id"
                "  FROM expenses WHERE receipt_id = ?",
                [rid],
            ).fetchone()
            assert exp["category_id"] == 1
            assert exp["confidence_level"] == 4
            assert exp["rule_id"] is None
            assert Decimal(str(exp["amount_original"])) == Decimal("123.45")
            assert exp["currency_original"] == "RSD"
            assert exp["receipt_id"] == rid

            job = con.execute(
                "SELECT 1 FROM receipt_classification_jobs WHERE receipt_id = ?",
                [rid],
            ).fetchone()
            assert job is None
        finally:
            con.close()

        # Resolved receipt drops out of the stuck-receipts queue.
        queue = client.get("/api/receipts/queue").json()
        assert queue["items"] == []

    @patch("dinary.adapters.rates.service.get_rate", side_effect=_mock_get_rate)
    def test_resolve_montenegrin_receipt_stores_eur(self, _mock_rate, client, db):  # noqa: ARG002
        rid = _insert_receipt(url=_MNE_RECEIPT_URL, client_receipt_id="rcid-mne-resolve")
        _insert_job(rid, status="poisoned", last_error="boom")

        resp = client.post(f"/api/receipts/{rid}/resolve", json={"category_id": 1})
        assert resp.status_code == 204, resp.text

        con = storage.get_connection()
        try:
            exp = con.execute(
                "SELECT amount_original, currency_original FROM expenses WHERE receipt_id = ?",
                [rid],
            ).fetchone()
            assert Decimal(str(exp["amount_original"])) == Decimal("59.10")
            assert exp["currency_original"] == "EUR"
        finally:
            con.close()

    @patch("dinary.adapters.rates.service.get_rate", side_effect=_mock_get_rate)
    def test_resolve_with_tags_and_event_auto_tags(self, _mock_rate, client, db):  # noqa: ARG002
        con = storage.get_connection()
        try:
            con.execute("UPDATE events SET auto_tags = '[2]' WHERE id = 1")
        finally:
            con.close()

        rid = _insert_receipt()
        _insert_job(rid, status="pending")

        resp = client.post(
            f"/api/receipts/{rid}/resolve",
            json={"category_id": 1, "tag_ids": [1], "event_id": 1, "comment": "manual"},
        )
        assert resp.status_code == 204, resp.text

        con = storage.get_connection()
        try:
            exp = con.execute(
                "SELECT id, event_id, comment FROM expenses WHERE receipt_id = ?",
                [rid],
            ).fetchone()
            assert exp["event_id"] == 1
            assert exp["comment"] == "manual"

            tag_ids = {
                row[0]
                for row in con.execute(
                    "SELECT tag_id FROM expense_tags WHERE expense_id = ?",
                    [exp["id"]],
                ).fetchall()
            }
            assert tag_ids == {1, 2}
        finally:
            con.close()

    def test_resolve_unknown_receipt_returns_404(self, client, db):  # noqa: ARG002
        resp = client.post("/api/receipts/999999/resolve", json={"category_id": 1})
        assert resp.status_code == 404

    def test_resolve_without_job_returns_409(self, client, db):  # noqa: ARG002
        rid = _insert_receipt()  # no receipt_classification_jobs row

        resp = client.post(f"/api/receipts/{rid}/resolve", json={"category_id": 1})
        assert resp.status_code == 409

    def test_resolve_unknown_category_returns_422(self, client, db):  # noqa: ARG002
        rid = _insert_receipt()
        _insert_job(rid, status="pending")

        resp = client.post(f"/api/receipts/{rid}/resolve", json={"category_id": 999})
        assert resp.status_code == 422

    def test_resolve_unknown_event_returns_422(self, client, db):  # noqa: ARG002
        rid = _insert_receipt()
        _insert_job(rid, status="pending")

        resp = client.post(
            f"/api/receipts/{rid}/resolve",
            json={"category_id": 1, "event_id": 999},
        )
        assert resp.status_code == 422

    def test_resolve_unknown_tag_returns_422(self, client, db):  # noqa: ARG002
        rid = _insert_receipt()
        _insert_job(rid, status="pending")

        resp = client.post(
            f"/api/receipts/{rid}/resolve",
            json={"category_id": 1, "tag_ids": [999]},
        )
        assert resp.status_code == 422

    def test_resolve_undecodable_url_returns_422(self, client, db):  # noqa: ARG002
        rid = _insert_receipt(url="https://suf.purs.gov.rs/v/", client_receipt_id="rcid-no-vl")
        _insert_job(rid, status="pending")

        resp = client.post(f"/api/receipts/{rid}/resolve", json={"category_id": 1})
        assert resp.status_code == 422
