"""Background task: drain receipt_classification_jobs queue.

See "Classification Layer" in specs/architecture/architecture.md.
"""

import asyncio
import contextlib
import logging
from datetime import UTC, datetime

import httpx
from sr_invoice_parser.exceptions import ParserParseException, ParserRequestException

from dinary.adapters.llmbroker import LLMBroker
from dinary.adapters.serbian_receipt_parser import ParsedReceipt, parse_receipt
from dinary.background.classification.item_normalizer import normalize_item_name
from dinary.background.classification.llm_client import (
    ClassificationResult,
    classify_receipt,
    load_categories,
    load_tags,
)
from dinary.background.classification.persist import (
    RateMissingError,
    persist_classification_results,
    write_fetch_fallback_metadata,
)
from dinary.background.classification.store_resolver import resolve_store
from dinary.config import settings
from dinary.db import storage
from dinary.db.classification_rules import classify_by_rules
from dinary.db.receipts import (
    ReceiptItemRow,
    ReceiptJobRow,
    claim_next_job,
    complete_job,
    get_receipt_items,
    poison_job,
    release_job,
    save_parsed_receipt,
)

logger = logging.getLogger(__name__)

_wakeup_event: asyncio.Event | None = None


def notify_new_receipt() -> None:
    """Signal the drain loop that a new receipt is ready — wakes it immediately."""
    if _wakeup_event is not None:
        _wakeup_event.set()


async def receipt_classification_task(broker: LLMBroker) -> None:
    global _wakeup_event  # noqa: PLW0603
    _wakeup_event = asyncio.Event()
    if not settings.receipt_classification_enabled:
        return
    logger.info("Receipt classification drain started")

    await _drain_all_pending(broker)  # pick up jobs surviving a restart

    while True:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(_wakeup_event.wait(), timeout=300)
        _wakeup_event.clear()  # clear BEFORE drain so receipts arriving during drain re-set it
        await _drain_all_pending(broker)


def _claim_all_pending() -> list[ReceiptJobRow]:
    conn = storage.get_connection()
    try:
        jobs: list[ReceiptJobRow] = []
        while True:
            job = claim_next_job(conn)
            if job is None:
                break
            jobs.append(job)
        return jobs
    finally:
        conn.close()


async def _drain_all_pending(broker: LLMBroker) -> None:
    jobs = _claim_all_pending()
    if jobs:
        await asyncio.gather(
            *[_process_job(job, broker) for job in jobs],
            return_exceptions=True,
        )


async def _process_job(job: ReceiptJobRow, broker: LLMBroker) -> None:
    logger.info("Receipt drain: processing receipt_id=%s", job.receipt_id)
    try:
        if job.parsed_at is None:
            parsed = await parse_receipt(job.url)
            _save_parsed(job.receipt_id, parsed)
            job = _with_parsed_data(job, parsed)

        store_id = await _ensure_store(job, broker)

        conn = storage.get_connection()
        try:
            items = get_receipt_items(conn, job.receipt_id)

            has_expenses = bool(
                conn.execute(
                    "SELECT 1 FROM expenses WHERE receipt_id = ? LIMIT 1",
                    [job.receipt_id],
                ).fetchone(),
            )

            if not items or has_expenses:
                if has_expenses:
                    logger.info(
                        "Receipt drain: expenses already present for receipt_id=%s"
                        " — completing stale job",
                        job.receipt_id,
                    )
                elif not items:
                    logger.warning(
                        "Receipt drain: no items found for receipt_id=%s"
                        " — completing job with no expenses",
                        job.receipt_id,
                    )
                conn.execute("BEGIN IMMEDIATE")
                try:
                    complete_job(conn, job.receipt_id)
                    conn.execute("COMMIT")
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
                return
        finally:
            conn.close()  # released before any LLM await

        await _classify_and_persist(broker, job, items, store_id)
        logger.info("Receipt drain: completed receipt_id=%s", job.receipt_id)

    except (ParserRequestException, OSError, RateMissingError) as exc:
        logger.warning(
            "Transient error for receipt_id=%s (%s) — releasing for retry",
            job.receipt_id,
            exc,
        )
        _release(job.receipt_id, job.claim_token)
    except ParserParseException as exc:
        logger.error(
            "Permanent parse error for receipt_id=%s — poisoning: %s",
            job.receipt_id,
            exc,
        )
        _poison(job.receipt_id, str(exc))
    except Exception as exc:
        logger.exception("Permanent error for receipt_id=%s — poisoning", job.receipt_id)
        _poison(job.receipt_id, str(exc))


def _release(receipt_id: int, claim_token: str) -> None:
    conn = storage.get_connection()
    try:
        release_job(conn, receipt_id, claim_token)
    finally:
        conn.close()


def _poison(receipt_id: int, error: str) -> None:
    conn = storage.get_connection()
    try:
        poison_job(conn, receipt_id, error)
    finally:
        conn.close()


def _save_parsed(receipt_id: int, parsed: ParsedReceipt) -> None:
    conn = storage.get_connection()
    try:
        save_parsed_receipt(conn, receipt_id, parsed)
        if parsed.used_journal_fallback:
            write_fetch_fallback_metadata(conn, parsed.invoice_number, "journal fallback used")
        else:
            conn.execute(
                "DELETE FROM app_metadata WHERE key = 'receipt_fetch_fallback_last'",
            )
    finally:
        conn.close()

    if not parsed.total_ok:
        logger.warning(
            "receipt_id=%s total mismatch: items=%.2f receipt=%.2f",
            receipt_id,
            parsed.items_total,
            parsed.total_amount,
        )


def _with_parsed_data(job: ReceiptJobRow, parsed: ParsedReceipt) -> ReceiptJobRow:
    return ReceiptJobRow(
        receipt_id=job.receipt_id,
        url=job.url,
        store_name_raw=parsed.store_name,
        store_pib_raw=parsed.store_pib,
        invoice_number=parsed.invoice_number,
        parsed_at=datetime.now(UTC).isoformat(),
        used_journal_fallback=parsed.used_journal_fallback,
        claim_token=job.claim_token,
    )


async def _ensure_store(job: ReceiptJobRow, broker: LLMBroker) -> int | None:
    if not job.store_name_raw and not job.store_pib_raw:
        return None

    with storage.connection() as conn:
        existing = conn.execute(
            "SELECT store_id FROM receipts WHERE id = ?",
            [job.receipt_id],
        ).fetchone()
        if existing and existing[0]:
            return int(existing[0])

    try:
        store_id = await resolve_store(broker, job.store_pib_raw, job.store_name_raw)
        with storage.connection() as conn:
            conn.execute(
                "UPDATE receipts SET store_id = ? WHERE id = ?",
                [store_id, job.receipt_id],
            )
        return store_id
    except (httpx.HTTPError, OSError):
        logger.warning(
            "Store resolution failed for receipt_id=%s",
            job.receipt_id,
            exc_info=True,
        )
        return None


def _run_rules_pass(
    conn,
    items: list[ReceiptItemRow],
    store_id: int | None,
) -> tuple[dict[int, tuple[int, int, list[int]]], list[tuple[int, str]]]:
    """Classify items by rules; return (rule_hits, llm_queue) for remaining items."""
    rule_hits: dict[int, tuple[int, int, list[int]]] = {}
    llm_queue: list[tuple[int, str]] = []
    for item in items:
        norm = normalize_item_name(item.name_raw)
        rule = classify_by_rules(conn, store_id, norm)
        if rule and rule[1] > 1:
            rule_hits[item.id] = rule
        else:
            llm_queue.append((item.id, norm))
    return rule_hits, llm_queue


async def _run_llm_pass(
    broker: LLMBroker,
    job: ReceiptJobRow,
    llm_queue: list[tuple[int, str]],
    categories: dict[int, str],
    tags: dict[int, str],
) -> tuple[dict[int, ClassificationResult], bool]:
    """Call LLM for queued items; return (results keyed by item_id, used_fallback)."""
    if not llm_queue:
        return {}, False
    normalized_names = [name for _, name in llm_queue]
    results, used_fallback = await classify_receipt(
        broker,
        normalized_names,
        job.store_name_raw or "Unknown Store",
        categories,
        tags,
        context_id=job.receipt_id,
    )
    if len(results) != len(llm_queue):
        logger.warning(
            "LLM returned %d results for %d queued items for receipt_id=%s"
            " — trailing items will be unclassified",
            len(results),
            len(llm_queue),
            job.receipt_id,
        )
    return (
        {item_id: result for (item_id, _), result in zip(llm_queue, results, strict=False)},
        used_fallback,
    )


def _compute_classifications(
    items: list[ReceiptItemRow],
    rule_hits: dict[int, tuple[int, int, list[int]]],
    llm_results: dict[int, ClassificationResult],
    total_penalty: int,
) -> dict[int, tuple[int | None, int]]:
    """Merge rule and LLM results into {item_id: (category_id, confidence)}."""
    result: dict[int, tuple[int | None, int]] = {}
    for item in items:
        if item.id in rule_hits:
            cat_id, conf, _ = rule_hits[item.id]
            result[item.id] = (cat_id, conf)
        else:
            llm = llm_results.get(item.id)
            if llm is None:
                result[item.id] = (None, 1)
            else:
                conf = max(1, llm.confidence_level - total_penalty)
                result[item.id] = (llm.category_id, conf)
    return result


async def _classify_and_persist(
    broker: LLMBroker,
    job: ReceiptJobRow,
    items: list[ReceiptItemRow],
    store_id: int | None,
) -> None:
    # Connection 1: sync reads (released before LLM call)
    conn = storage.get_connection()
    try:
        if conn.execute(
            "SELECT 1 FROM expenses WHERE receipt_id = ? LIMIT 1",
            [job.receipt_id],
        ).fetchone():
            conn.execute("BEGIN IMMEDIATE")
            try:
                complete_job(conn, job.receipt_id)
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            return
        rule_hits, llm_queue = _run_rules_pass(conn, items, store_id)
        categories = load_categories(conn)
        tags = load_tags(conn)
    finally:
        conn.close()

    llm_results, used_failover = await _run_llm_pass(broker, job, llm_queue, categories, tags)
    total_penalty = (1 if job.used_journal_fallback else 0) + (1 if used_failover else 0)
    classifications = _compute_classifications(items, rule_hits, llm_results, total_penalty)

    # Connection 2: write transaction
    conn = storage.get_connection()
    try:
        persist_classification_results(
            conn,
            job,
            items,
            classifications,
            rule_hits,
            llm_results,
            store_id,
        )
    finally:
        conn.close()
