import base64
import struct
from datetime import UTC, datetime
from decimal import Decimal

import allure

from dinary.adapters.receipts.serbian import decode_qr_payload


def _build_vl(amount_units: int, epoch_ms: int) -> str:
    """Mirror webapp/tests/composable-receipt.test.js buildVlPayload.

    bytes 25..32: amount (uint64 little-endian, in 1/10000 units)
    bytes 33..40: milliseconds since epoch (big-endian uint64)
    """
    buf = bytearray(64)
    struct.pack_into("<Q", buf, 25, amount_units)
    struct.pack_into(">Q", buf, 33, epoch_ms)
    return base64.b64encode(bytes(buf)).decode()


@allure.epic("Receipts")
@allure.feature("Pipeline")
@allure.story("QR payload decoding")
class TestDecodeQrPayload:
    def test_valid_payload(self):
        purchase_dt = datetime(2026, 5, 4, 12, 30, 0, tzinfo=UTC)
        epoch_ms = int(purchase_dt.timestamp() * 1000)
        vl = _build_vl(1234500, epoch_ms)

        payload = decode_qr_payload(f"https://suf.purs.gov.rs/v/?vl={vl}")

        assert payload is not None
        assert payload.amount == Decimal("123.45")
        assert payload.purchase_datetime == purchase_dt

    def test_missing_vl_returns_none(self):
        assert decode_qr_payload("https://suf.purs.gov.rs/v/") is None

    def test_malformed_base64_returns_none(self):
        assert decode_qr_payload("https://suf.purs.gov.rs/v/?vl=not-base64!!!") is None

    def test_truncated_buffer_returns_none(self):
        vl = base64.b64encode(b"too short").decode()
        assert decode_qr_payload(f"https://suf.purs.gov.rs/v/?vl={vl}") is None
