"""POST /api/receipts endpoint tests."""

import allure

from dinary.db import storage

from _api_helpers import db  # noqa: F401 (autouse fixture)


@allure.epic("Receipts")
@allure.feature("API")
class TestPostReceipt:
    def test_create_receipt_ok(self, client, db):  # noqa: ARG002
        resp = client.post(
            "/api/receipts",
            json={"client_receipt_id": "r1", "url": "https://suf.purs.gov.rs/v/?vl=test"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "ok"
        assert isinstance(data["receipt_id"], int)

    def test_duplicate_same_url_returns_duplicate(self, client, db):  # noqa: ARG002
        body = {"client_receipt_id": "r2", "url": "https://suf.purs.gov.rs/v/?vl=abc"}
        r1 = client.post("/api/receipts", json=body)
        assert r1.status_code == 200
        r2 = client.post("/api/receipts", json=body)
        assert r2.status_code == 200
        assert r2.json()["status"] == "duplicate"
        assert r2.json()["receipt_id"] == r1.json()["receipt_id"]

    def test_duplicate_different_url_returns_409(self, client, db):  # noqa: ARG002
        client.post(
            "/api/receipts",
            json={"client_receipt_id": "r3", "url": "https://suf.purs.gov.rs/v/?vl=first"},
        )
        resp = client.post(
            "/api/receipts",
            json={"client_receipt_id": "r3", "url": "https://suf.purs.gov.rs/v/?vl=second"},
        )
        assert resp.status_code == 409

    def test_job_queued(self, client, db):  # noqa: ARG002
        resp = client.post(
            "/api/receipts",
            json={"client_receipt_id": "r4", "url": "https://suf.purs.gov.rs/v/?vl=xyz"},
        )
        receipt_id = resp.json()["receipt_id"]
        con = storage.get_connection()
        try:
            row = con.execute(
                "SELECT status FROM receipt_classification_jobs WHERE receipt_id = ?",
                [receipt_id],
            ).fetchone()
        finally:
            con.close()
        assert row is not None
        assert row[0] == "pending"

    def test_missing_url_field(self, client, db):  # noqa: ARG002
        resp = client.post(
            "/api/receipts",
            json={"client_receipt_id": "r5"},
        )
        assert resp.status_code == 422

    def test_empty_client_receipt_id(self, client, db):  # noqa: ARG002
        resp = client.post(
            "/api/receipts",
            json={"client_receipt_id": "", "url": "https://suf.purs.gov.rs/v/?vl=test"},
        )
        assert resp.status_code == 422
