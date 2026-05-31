"""Background task: drain receipt_classification_jobs queue.

See "Classification Layer" in specs/architecture/architecture.md.
"""

import asyncio
import contextlib
import logging
import sqlite3
from datetime import UTC, datetime, timedelta

import httpx

from dinary.adapters.llmbroker import LLMBroker
from dinary.adapters.serbian_receipt_parser import (
    ParsedReceipt,
    ParserParseError,
    ParserRequestError,
    parse_receipt,
)
from dinary.background.classification.item_normalizer import normalize_item_name
from dinary.background.classification.persist import (
    RateMissingError,
    persist_classification_results,
    write_fetch_fallback_metadata,
)
from dinary.background.classification.receipt_classifier import (
    ClassificationResult,
    classify_receipt,
    load_categories,
    load_tags,
)
from dinary.background.classification.store_resolver import resolve_store
from dinary.config import settings
from dinary.db import storage
from dinary.db.classification_rules import RuleHit, classify_by_rules
from dinary.db.receipts import (
    ReceiptItemRow,
    ReceiptJobRow,
    claim_next_job,
    get_receipt_items,
    poison_job,
    release_job,
    save_parsed_receipt,
)

logger = logging.getLogger(__name__)

_FIFTEEN_MINUTES = 900
_ONE_DAY = 86400
_DAILY_THRESHOLD = 100  # retry_count at which 15-min phase ends (~1 day elapsed)


class ClassificationExhaustedError(Exception):
    """All provider attempts failed for a receipt; fallback will be applied."""


class InsufficientCategoriesError(Exception):
    """Fewer than 5 active categories; installation is broken."""


def _retry_delay(retry_count: int) -> int:
    # No ceiling: the schedule tops out at one day but never stops retrying (by design).
    if retry_count == 0:
        return 0
    if retry_count == 1:
        return 3
    if retry_count == 2:
        return 60
    if retry_count < _DAILY_THRESHOLD:
        return _FIFTEEN_MINUTES
    return _ONE_DAY


_wakeup_event: asyncio.Event | None = None
_wakeup_loop: asyncio.AbstractEventLoop | None = None


def _register_wake_channel(
    event: asyncio.Event,
    loop: asyncio.AbstractEventLoop,
) -> None:
    global _wakeup_event, _wakeup_loop  # noqa: PLW0603
    _wakeup_event = event
    _wakeup_loop = loop


def _clear_wake_channel() -> None:
    global _wakeup_event, _wakeup_loop  # noqa: PLW0603
    _wakeup_event = None
    _wakeup_loop = None


def notify_new_receipt() -> None:
    """Signal the drain loop that a new receipt is ready.

    Thread-safe: safe to call from the event loop thread, from an
    asyncio.to_thread worker, or from a regular sync context.
    """
    ev = _wakeup_event
    loop = _wakeup_loop
    if ev is None or loop is None or loop.is_closed():
        return
    try:
        loop.call_soon_threadsafe(ev.set)
    except RuntimeError:
        return


def _schedule_wakeup(delay_sec: float) -> None:
    """Schedule the drain loop to wake after delay_sec seconds."""
    # Multiple concurrent failures each register a timer; asyncio.Event.set() is idempotent.
    ev = _wakeup_event
    loop = _wakeup_loop
    if ev is None or loop is None or loop.is_closed():
        return
    try:
        loop.call_soon_threadsafe(loop.call_later, delay_sec, ev.set)
    except RuntimeError:
        return


async def receipt_classification_task(broker: LLMBroker) -> None:
    event = asyncio.Event()
    loop = asyncio.get_running_loop()
    _register_wake_channel(event, loop)
    try:
        if not settings.receipt_classification_enabled:
            return
        logger.info("Receipt classification drain started")

        await _drain_all_pending(broker)  # pick up jobs surviving a restart

        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(event.wait(), timeout=300)
            event.clear()  # clear BEFORE drain so receipts arriving during drain re-set it
            await _drain_all_pending(broker)
    finally:
        _clear_wake_channel()


def _claim_all_pending() -> list[ReceiptJobRow]:
    with storage.connection() as conn:
        jobs: list[ReceiptJobRow] = []
        while True:
            job = claim_next_job(conn)
            if job is None:
                break
            jobs.append(job)
        return jobs


async def _drain_all_pending(broker: LLMBroker) -> None:
    jobs = await asyncio.to_thread(_claim_all_pending)
    if not jobs:
        return
    results = await asyncio.gather(
        *[_process_job(job, broker) for job in jobs],
        return_exceptions=True,
    )
    for job, result in zip(jobs, results, strict=True):
        if isinstance(result, asyncio.CancelledError):
            logger.warning(
                "_process_job cancelled for receipt_id=%s",
                job.receipt_id,
            )
        elif isinstance(result, Exception):
            logger.error(
                "Unhandled exception in _process_job for receipt_id=%s",
                job.receipt_id,
                exc_info=result,
            )
        elif isinstance(result, BaseException):
            logger.error(
                "Unexpected BaseException in _process_job for receipt_id=%s",
                job.receipt_id,
                exc_info=result,
            )


async def _process_job(job: ReceiptJobRow, broker: LLMBroker) -> None:
    logger.info("Receipt drain: processing receipt_id=%s", job.receipt_id)
    try:
        if job.parsed_at is None:
            parsed = await parse_receipt(job.url)
            await asyncio.to_thread(_save_parsed, job.receipt_id, parsed)
            job = _with_parsed_data(job, parsed)

        store_info = await _ensure_store(job, broker)
        store_id = store_info[0] if store_info else None
        chain_id = store_info[1] if store_info else None

        items = await asyncio.to_thread(_get_items, job.receipt_id)
        await _classify_and_persist(broker, job, items, store_id, chain_id)
        logger.info("Receipt drain: completed receipt_id=%s", job.receipt_id)

    except (
        ParserRequestError,
        httpx.HTTPError,
        ConnectionError,
        RateMissingError,
        InsufficientCategoriesError,
    ) as exc:
        # These are transient: retries until the condition clears.
        # delay uses the pre-increment count: retry_count=0 → delay=0 → one free immediate retry.
        delay = _retry_delay(job.retry_count)
        new_retry_count = job.retry_count + 1
        retry_after = (
            None
            if delay == 0
            else (datetime.now(UTC) + timedelta(seconds=delay)).strftime("%Y-%m-%d %H:%M:%S")
        )
        logger.warning(
            "Transient error for receipt_id=%s (%s) — releasing for retry in %ds (attempt %d)",
            job.receipt_id,
            exc,
            delay,
            new_retry_count,
        )
        try:
            await asyncio.to_thread(
                _release,
                job.receipt_id,
                job.claim_token,
                new_retry_count,
                retry_after,
            )
        except Exception:
            logger.exception(
                "Failed to release receipt_id=%s — stale timeout will recover",
                job.receipt_id,
            )
            return
        if delay == 0:
            notify_new_receipt()
        else:
            _schedule_wakeup(delay)
    except ParserParseError as exc:
        logger.error(
            "Permanent parse error for receipt_id=%s — poisoning: %s",
            job.receipt_id,
            exc,
        )
        await asyncio.to_thread(_poison, job.receipt_id, str(exc))
    except Exception as exc:
        logger.exception("Permanent error for receipt_id=%s — poisoning", job.receipt_id)
        await asyncio.to_thread(_poison, job.receipt_id, str(exc))


def _release(
    receipt_id: int,
    claim_token: str,
    retry_count: int,
    retry_after: str | None,
) -> None:
    with storage.connection() as conn:
        release_job(conn, receipt_id, claim_token, retry_count, retry_after)


def _poison(receipt_id: int, error: str) -> None:
    with storage.connection() as conn:
        poison_job(conn, receipt_id, error)


def _save_parsed(receipt_id: int, parsed: ParsedReceipt) -> None:
    with storage.connection() as conn:
        save_parsed_receipt(conn, receipt_id, parsed)
        if parsed.used_journal_fallback:
            write_fetch_fallback_metadata(conn, parsed.invoice_number, "journal fallback used")
        else:
            conn.execute(
                "DELETE FROM app_metadata WHERE key = 'receipt_fetch_fallback_last'",
            )

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
        retry_count=job.retry_count,
    )


def _check_store_already_resolved(receipt_id: int) -> tuple[int, int | None] | None:
    with storage.connection() as conn:
        cur = conn.cursor()
        cur.row_factory = sqlite3.Row
        row = cur.execute(
            """
            SELECT s.id AS store_id, s.chain_id
              FROM receipts r
              JOIN stores s ON s.id = r.store_id
             WHERE r.id = ?
            """,
            [receipt_id],
        ).fetchone()
    if row is None:
        return None
    return int(row["store_id"]), row["chain_id"]


def _save_store_to_receipt(receipt_id: int, store_id: int) -> None:
    with storage.connection() as conn:
        conn.execute(
            "UPDATE receipts SET store_id = ? WHERE id = ?",
            [store_id, receipt_id],
        )


async def _ensure_store(job: ReceiptJobRow, broker: LLMBroker) -> tuple[int, int | None] | None:
    if not job.store_name_raw and not job.store_pib_raw:
        return None

    result = await asyncio.to_thread(_check_store_already_resolved, job.receipt_id)
    if result is not None:
        return result

    store_id, chain_id = await resolve_store(broker, job.store_pib_raw, job.store_name_raw)
    await asyncio.to_thread(_save_store_to_receipt, job.receipt_id, store_id)
    return store_id, chain_id


def _run_rules_pass(
    conn: sqlite3.Connection,
    items: list[ReceiptItemRow],
    chain_id: int | None,
) -> tuple[dict[int, RuleHit], list[tuple[int, str]], dict[int, str]]:
    """Classify items by rules; return (rule_hits, llm_queue, norms) for remaining items."""
    rule_hits: dict[int, RuleHit] = {}
    llm_queue: list[tuple[int, str]] = []
    norms: dict[int, str] = {}
    for item in items:
        norm = normalize_item_name(item.name_raw)
        norms[item.id] = norm
        rule = classify_by_rules(conn, chain_id, norm)
        if rule and rule.confidence_level > 1:
            rule_hits[item.id] = rule
        else:
            llm_queue.append((item.id, norm))
    return rule_hits, llm_queue, norms


def _load_top_fallback_categories(n: int) -> list[int]:
    """Return top-n category IDs by recent usage, padded with active categories."""
    with storage.connection() as conn:
        cur = conn.cursor()
        cur.row_factory = sqlite3.Row
        active_count = cur.execute(
            "SELECT COUNT(*) AS active_count FROM categories WHERE is_active = 1",
        ).fetchone()["active_count"]
        if active_count < 5:
            raise InsufficientCategoriesError(
                f"only {active_count} active categories — need at least 5",
            )

        rows = cur.execute(
            """
            SELECT category_id, COUNT(*) AS cnt
              FROM expenses
             WHERE category_id IS NOT NULL
               AND datetime >= datetime('now', '-3 months')
             GROUP BY category_id
             ORDER BY cnt DESC
             LIMIT ?
            """,
            [n],
        ).fetchall()
        result = [int(r["category_id"]) for r in rows]

        if len(result) < n:
            if result:
                placeholders = ",".join("?" * len(result))
                not_in_clause = f"AND id NOT IN ({placeholders})"
                pad_params: list = [*result, n - len(result)]
            else:
                not_in_clause = ""
                pad_params = [n - len(result)]
            pad_rows = cur.execute(
                f"""
                SELECT id FROM categories
                 WHERE is_active = 1
                   {not_in_clause}
                 ORDER BY id
                 LIMIT ?
                """,  # noqa: S608
                pad_params,
            ).fetchall()
            result.extend(int(r["id"]) for r in pad_rows)

    return result


async def _run_llm_pass(
    broker: LLMBroker,
    job: ReceiptJobRow,
    llm_queue: list[tuple[int, str]],
    categories: dict[int, str],
    tags: dict[int, str],
) -> dict[int, ClassificationResult]:
    """Call LLM for queued items; return results keyed by item_id.

    Raises ConnectionError when the broker is completely unavailable so the
    caller's transient-error handler retries the job instead of silently
    completing it with zero expenses.
    Raises ClassificationExhaustedError after all retry attempts fail.
    """
    if not llm_queue:
        return {}
    normalized_names = [name for _, name in llm_queue]
    max_attempts = max(1, min(3, broker.provider_count))
    for attempt in range(max_attempts):
        outcome = await classify_receipt(
            broker,
            normalized_names,
            job.store_name_raw or "Unknown Store",
            categories,
            tags,
            execution_id=job.receipt_id,
        )
        if outcome.broker_unavailable:
            raise ConnectionError(
                f"LLM broker unavailable for receipt_id={job.receipt_id}"
                " — all providers returned None, releasing for retry",
            )
        if not outcome.execution_failed:
            return {
                item_id: result
                for (item_id, _), result in zip(llm_queue, outcome.results, strict=True)
            }
        try:
            await outcome.execution.mark_failed()
        except Exception:
            logger.exception(
                "mark_failed raised for receipt_id=%s attempt %d — continuing",
                job.receipt_id,
                attempt + 1,
            )
        logger.warning(
            "classification execution_failed attempt %d/%d receipt_id=%s",
            attempt + 1,
            max_attempts,
            job.receipt_id,
        )

    raise ClassificationExhaustedError(
        f"receipt_id={job.receipt_id}: all {max_attempts} attempts failed",
    )


def _compute_classifications(
    items: list[ReceiptItemRow],
    rule_hits: dict[int, RuleHit],
    llm_results: dict[int, ClassificationResult],
    total_penalty: int,
) -> dict[int, tuple[int | None, int]]:
    """Merge rule and LLM results into {item_id: (category_id, confidence)}."""
    result: dict[int, tuple[int | None, int]] = {}
    for item in items:
        if item.id in rule_hits:
            hit = rule_hits[item.id]
            result[item.id] = (hit.category_id, hit.confidence_level)
        else:
            llm = llm_results.get(item.id)
            if llm is None:
                result[item.id] = (None, 1)
            else:
                conf = max(1, llm.confidence_level - total_penalty)
                result[item.id] = (llm.category_id, conf)
    return result


def _get_items(receipt_id: int) -> list[ReceiptItemRow]:
    with storage.connection() as conn:
        items = get_receipt_items(conn, receipt_id)
        if not items:
            raise RuntimeError(
                f"receipt_id={receipt_id}: no items after parsing — parse error",
            )
        return items


async def _classify_and_persist(
    broker: LLMBroker,
    job: ReceiptJobRow,
    items: list[ReceiptItemRow],
    store_id: int | None,
    chain_id: int | None,
) -> None:
    with storage.connection() as conn:
        rule_hits, llm_queue, norms = _run_rules_pass(conn, items, chain_id)
        categories = load_categories(conn)
        tags = load_tags(conn)

    try:
        llm_results = await _run_llm_pass(broker, job, llm_queue, categories, tags)
    except ClassificationExhaustedError:
        top_cats = await asyncio.to_thread(_load_top_fallback_categories, 6)
        primary_cat = top_cats[0]
        llm_results = {
            item_id: ClassificationResult(
                item_name_normalized=norm,
                category_id=primary_cat,
                confidence_level=1,
                alternative_category_ids=top_cats[1:],
            )
            for item_id, norm in llm_queue
        }

    total_penalty = 1 if job.used_journal_fallback else 0
    classifications = _compute_classifications(items, rule_hits, llm_results, total_penalty)

    await asyncio.to_thread(
        persist_classification_results,
        job,
        items,
        classifications,
        rule_hits,
        llm_results,
        store_id,
        chain_id,
        norms,
    )
