"""DB write path for receipt classification results."""

import dataclasses
import logging
import sqlite3
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from dinary.adapters.rates.service import get_rate
from dinary.adapters.receipts.dispatch import receipt_currency
from dinary.background.classification.item_normalizer import normalize_item_name
from dinary.background.classification.receipt_classifier import ClassificationResult
from dinary.background.sheet_logging import sheet_logging
from dinary.config import settings
from dinary.db.classification_rules import RuleHit, RuleSpec, create_or_update_rule
from dinary.db.expenses import enqueue_for_logging
from dinary.db.receipts import (
    ReceiptItemRow,
    ReceiptJobRow,
    complete_job,
    update_receipt_item,
)
from dinary.db.storage import connection, transaction
from dinary.sheets.sheet_mapping import resolve_event_auto_tag_ids

logger = logging.getLogger(__name__)


class RateMissingError(Exception):
    """Exchange rate unavailable; release job for retry."""


@dataclasses.dataclass(frozen=True, slots=True)
class _ReceiptContext:
    receipt_dt: datetime
    currency: str
    accounting_rate: Decimal
    auto_event_id: int | None
    event_auto_tag_ids: list[int]
    rule_hits: dict[int, RuleHit]
    llm_results: dict[int, ClassificationResult]
    store_id: int | None
    chain_id: int | None
    receipt_id: int
    llm_name: str | None


def _find_auto_attach_event(conn: sqlite3.Connection, receipt_dt: str) -> int | None:
    """Return id of the active auto-attach event covering receipt_dt, or None."""
    row = conn.execute(
        """
        SELECT id FROM events
         WHERE auto_attach_enabled = 1 AND is_active = 1
           AND date_from <= date(?) AND date_to >= date(?)
         ORDER BY date_from DESC
         LIMIT 1
        """,
        [receipt_dt, receipt_dt],
    ).fetchone()
    return int(row[0]) if row else None


def _write_single_item(
    conn: sqlite3.Connection,
    item: ReceiptItemRow,
    cat_id: int | None,
    conf: int,
    norm: str,
    ctx: _ReceiptContext,
) -> None:
    hit = ctx.rule_hits.get(item.id)
    llm_r = ctx.llm_results.get(item.id)

    if hit is not None:
        rule_id: int | None = hit.rule_id
        tag_ids_for_item = hit.tag_ids
    else:
        tag_ids_for_item = llm_r.tag_ids if llm_r else []
        rule_id = None
        if norm and cat_id is not None:
            rule_id = create_or_update_rule(
                conn,
                ctx.chain_id,
                norm,
                RuleSpec(
                    cat_id,
                    conf,
                    "llm",
                    alternative_category_ids=tuple(llm_r.alternative_category_ids if llm_r else []),
                    tag_ids=tuple(tag_ids_for_item),
                    llm_name=ctx.llm_name,
                ),
            )

    conn.execute(
        """
        INSERT INTO expenses
               (client_expense_id, datetime, amount, amount_original, currency_original,
                category_id, confidence_level, receipt_id, store_id, event_id, rule_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            str(uuid.uuid4()),
            ctx.receipt_dt,
            float(
                (Decimal(str(item.total_price)) * ctx.accounting_rate).quantize(Decimal("0.01")),
            ),
            item.total_price,
            ctx.currency,
            cat_id,
            conf,
            ctx.receipt_id,
            ctx.store_id,
            ctx.auto_event_id,
            rule_id,
        ],
    )
    expense_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    enqueue_for_logging(conn, expense_id)
    update_receipt_item(conn, item.id, norm, expense_id)

    seen: set[int] = set()
    for tag_id in [*tag_ids_for_item, *ctx.event_auto_tag_ids]:
        if tag_id not in seen:
            seen.add(tag_id)
            conn.execute(
                "INSERT OR IGNORE INTO expense_tags (expense_id, tag_id) VALUES (?, ?)",
                [expense_id, tag_id],
            )


def persist_classification_results(
    job: ReceiptJobRow,
    items: list[ReceiptItemRow],
    classifications: dict[int, tuple[int | None, int]],
    rule_hits: dict[int, RuleHit],
    llm_results: dict[int, ClassificationResult],
    store: tuple[int | None, int | None],
    norms: dict[int, str],
    llm_name: str | None = None,
) -> None:
    """Write classification results atomically; handles idempotency inside the transaction."""
    store_id, chain_id = store
    with connection() as conn:
        receipt_dt_row = conn.execute(
            "SELECT COALESCE(purchase_datetime, created_at) FROM receipts WHERE id = ?",
            [job.receipt_id],
        ).fetchone()
        _user_tz = ZoneInfo(settings.user_timezone)
        if receipt_dt_row and receipt_dt_row[0]:
            receipt_dt_obj = datetime.fromisoformat(receipt_dt_row[0]).astimezone(_user_tz)
        else:
            receipt_dt_obj = datetime.now(_user_tz)
        receipt_dt = receipt_dt_obj.isoformat()

        auto_event_id = _find_auto_attach_event(conn, receipt_dt)
        event_auto_tag_ids: list[int] = (
            resolve_event_auto_tag_ids(conn, auto_event_id) if auto_event_id is not None else []
        )
        currency = receipt_currency(job.url)
        if settings.accounting_currency.upper() != currency:
            try:
                receipt_date = date.fromisoformat(receipt_dt[:10])
                accounting_rate = get_rate(
                    conn,
                    receipt_date,
                    currency,
                    settings.accounting_currency,
                    offline=True,
                )
            except ValueError as exc:
                raise RateMissingError(
                    f"No {currency}/{settings.accounting_currency} rate for {receipt_dt[:10]}",
                ) from exc
        else:
            accounting_rate = Decimal(1)
        ctx = _ReceiptContext(
            receipt_dt=receipt_dt_obj,
            currency=currency,
            accounting_rate=accounting_rate,
            auto_event_id=auto_event_id,
            event_auto_tag_ids=event_auto_tag_ids,
            rule_hits=rule_hits,
            llm_results=llm_results,
            store_id=store_id,
            chain_id=chain_id,
            receipt_id=job.receipt_id,
            llm_name=llm_name,
        )
        with transaction(conn):
            if conn.execute(
                "SELECT 1 FROM expenses WHERE receipt_id = ? LIMIT 1",
                [job.receipt_id],
            ).fetchone():
                complete_job(conn, job.receipt_id)
                return

            for item in items:
                cat_id, conf = classifications.get(item.id, (None, 1))
                norm = norms.get(item.id) or normalize_item_name(item.name_raw)
                _write_single_item(conn, item, cat_id, conf, norm, ctx)

            complete_job(conn, job.receipt_id)
    try:
        sheet_logging.notify_new_work()
    except Exception:
        logger.exception(
            "notify_new_work failed after commit for receipt_id=%s",
            job.receipt_id,
        )


def write_fetch_fallback_metadata(
    conn: sqlite3.Connection,
    invoice_number: str,
    reason: str,
) -> None:
    now = datetime.now(UTC).isoformat()
    value = f"{now} | invoice: {invoice_number} | reason: {reason}"
    with transaction(conn):
        conn.execute(
            "INSERT INTO app_metadata (key, value) VALUES ('receipt_fetch_fallback_last', ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            [value],
        )
        conn.execute(
            "INSERT INTO app_metadata (key, value) VALUES ('receipt_fetch_fallback_count', '1')"
            " ON CONFLICT(key) DO UPDATE SET value ="
            "   CAST(CAST(value AS INTEGER) + 1 AS TEXT)",
        )
