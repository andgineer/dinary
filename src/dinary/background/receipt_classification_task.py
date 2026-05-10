"""Background task: drain receipt_classification_jobs queue.

Processes one job per interval:
1. Parse receipt URL (if not yet parsed) — transient failures release for retry.
2. Resolve store (PIB cache or LLM chain-name call).
3. Normalize item names.
4. Rules lookup per item.
5. Single LLM call for unmatched items.
6. Apply confidence penalties (journal fallback: −1, failover: −1, min 1).
7. Aggregate items by category → INSERT expenses (amount = sum, conf = MIN).
8. Update receipt_items and create classification rules (conf ≥ 2 for resolved;
   conf=1 cached for LLM-categorised but penalised-to-floor items).
9. Trim llm_call_log to last 200 rows.
"""

import asyncio
import contextlib
import logging
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime, timedelta

import httpx
from sr_invoice_parser.exceptions import ParserParseException, ParserRequestException

from dinary.config import settings
from dinary.services import ledger_repo
from dinary.services.classification_rules import (
    RuleSpec,
    classify_by_rules,
    create_or_update_rule,
)
from dinary.services.item_normalizer import normalize_item_name
from dinary.services.llm_client import (
    AllProvidersExhausted,
    ClassificationResult,
    ProviderPool,
    ReceiptContext,
)
from dinary.services.receipt_parser import ParsedReceipt, parse_receipt
from dinary.services.receipt_repo import (
    ItemClassification,
    ReceiptItemRow,
    ReceiptJobRow,
    claim_next_job,
    complete_job,
    get_receipt_items,
    poison_job,
    release_job,
    save_parsed_receipt,
    trim_llm_call_log,
    update_receipt_item,
)
from dinary.services.store_resolver import resolve_store

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Circuit breaker for LLM provider exhaustion
# Same pattern as the sheet-logging drain: exponential backoff starting at 60 s,
# capped at 30 min.  Resets on any successful drain cycle.
# ---------------------------------------------------------------------------

_llm_backoff_until: datetime | None = None
_llm_current_backoff_sec: float = 0.0
_LLM_BACKOFF_INITIAL_SEC = 60.0
_LLM_BACKOFF_MAX_SEC = 1800.0

_wakeup_event: asyncio.Event | None = None


def notify_new_receipt() -> None:
    """Signal the drain loop that a new receipt is ready — wakes it immediately."""
    if _wakeup_event is not None:
        _wakeup_event.set()


def _activate_llm_backoff() -> None:
    global _llm_backoff_until, _llm_current_backoff_sec  # noqa: PLW0603
    if _llm_current_backoff_sec == 0:
        _llm_current_backoff_sec = _LLM_BACKOFF_INITIAL_SEC
    else:
        _llm_current_backoff_sec = min(_llm_current_backoff_sec * 2, _LLM_BACKOFF_MAX_SEC)
    _llm_backoff_until = datetime.now(UTC) + timedelta(seconds=_llm_current_backoff_sec)
    logger.warning("Receipt drain circuit breaker: backoff %.0fs", _llm_current_backoff_sec)


def _reset_llm_backoff() -> None:
    global _llm_backoff_until, _llm_current_backoff_sec  # noqa: PLW0603
    _llm_backoff_until = None
    _llm_current_backoff_sec = 0.0


async def receipt_classification_task() -> None:
    global _wakeup_event  # noqa: PLW0603
    _wakeup_event = asyncio.Event()
    interval = settings.receipt_drain_interval_sec
    if interval <= 0:
        return
    logger.info("Receipt classification drain started (interval=%.0fs)", interval)
    loop = asyncio.get_running_loop()
    # Pretend last drain happened one interval ago so the first receipt (or
    # the first periodic tick) fires immediately — no forced startup delay.
    last_drain_at = loop.time() - interval
    while True:
        _wakeup_event.clear()
        cooldown = max(0.0, last_drain_at + interval - loop.time())
        if cooldown > 0:
            # Still inside cooldown — wait it out unconditionally.
            await asyncio.sleep(cooldown)
        else:
            # Cooldown expired — wake on next receipt or the periodic interval.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(_wakeup_event.wait(), timeout=interval)
        try:
            await _drain_one()
        except Exception:
            logger.exception("Receipt classification drain error")
        last_drain_at = loop.time()


async def _drain_one() -> None:
    if _llm_backoff_until is not None and datetime.now(UTC) < _llm_backoff_until:
        logger.debug("Receipt drain: LLM circuit breaker active — skipping sweep")
        return

    job = await asyncio.to_thread(_claim_job)
    if job is None:
        return

    logger.info("Receipt drain: processing receipt_id=%s", job.receipt_id)
    try:
        await _process_job(job)
        _reset_llm_backoff()
    except AllProvidersExhausted:
        logger.warning(
            "All LLM providers exhausted for receipt_id=%s — releasing for retry",
            job.receipt_id,
        )
        await asyncio.to_thread(_release, job.receipt_id, job.claim_token)
        _activate_llm_backoff()
    except (ParserRequestException, OSError) as exc:
        logger.warning(
            "Transient network error for receipt_id=%s (%s) — releasing for retry",
            job.receipt_id,
            exc,
        )
        await asyncio.to_thread(_release, job.receipt_id, job.claim_token)
    except ParserParseException as exc:
        logger.error("Permanent parse error for receipt_id=%s — poisoning: %s", job.receipt_id, exc)
        await asyncio.to_thread(_poison, job.receipt_id, str(exc))
    except Exception as exc:
        logger.exception("Permanent error for receipt_id=%s — poisoning", job.receipt_id)
        await asyncio.to_thread(_poison, job.receipt_id, str(exc))


def _claim_job() -> ReceiptJobRow | None:
    conn = ledger_repo.get_connection()
    try:
        return claim_next_job(conn)
    finally:
        conn.close()


def _release(receipt_id: int, claim_token: str) -> None:
    conn = ledger_repo.get_connection()
    try:
        release_job(conn, receipt_id, claim_token)
    finally:
        conn.close()


def _poison(receipt_id: int, error: str) -> None:
    conn = ledger_repo.get_connection()
    try:
        poison_job(conn, receipt_id, error)
    finally:
        conn.close()


async def _process_job(job: ReceiptJobRow) -> None:
    pool = ProviderPool()

    if job.parsed_at is None:
        parsed = await asyncio.to_thread(parse_receipt, job.url)
        await asyncio.to_thread(_save_parsed, job.receipt_id, parsed)
        job = _with_parsed_data(job, parsed)

    store_id = await _ensure_store(job, pool)

    conn = ledger_repo.get_connection()
    try:
        items = get_receipt_items(conn, job.receipt_id)

        # Idempotency: a prior run may have committed expenses but crashed before
        # complete_job.  Skip classification and just remove the stale job entry.
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

        await _classify_and_persist(conn, pool, job, items, store_id)
    finally:
        conn.close()

    logger.info("Receipt drain: completed receipt_id=%s", job.receipt_id)


def _save_parsed(receipt_id: int, parsed: ParsedReceipt) -> None:
    conn = ledger_repo.get_connection()
    try:
        # Receipt rows committed first; metadata only written on success so a
        # failed save never leaves stale healthcheck state.
        save_parsed_receipt(conn, receipt_id, parsed)
        if parsed.used_journal_fallback:
            _write_fetch_fallback_metadata(conn, parsed.invoice_number, "journal fallback used")
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
        parsed_at="now",
        used_journal_fallback=parsed.used_journal_fallback,
        claim_token=job.claim_token,
    )


async def _ensure_store(job: ReceiptJobRow, pool: ProviderPool) -> int | None:
    if not job.store_name_raw and not job.store_pib_raw:
        return None

    conn = ledger_repo.get_connection()
    try:
        existing = conn.execute(
            "SELECT store_id FROM receipts WHERE id = ?",
            [job.receipt_id],
        ).fetchone()
        if existing and existing[0]:
            return int(existing[0])

        try:
            store_id = await resolve_store(conn, pool, job.store_pib_raw, job.store_name_raw)
            conn.execute(
                "UPDATE receipts SET store_id = ? WHERE id = ?",
                [store_id, job.receipt_id],
            )
            return store_id
        except sqlite3.OperationalError:
            raise
        except (httpx.HTTPError, AllProvidersExhausted, OSError):
            logger.warning(
                "Store resolution failed for receipt_id=%s",
                job.receipt_id,
                exc_info=True,
            )
            return None
    finally:
        conn.close()


def _load_categories(conn: sqlite3.Connection) -> dict[int, str]:
    rows = conn.execute(
        "SELECT c.id, cg.name, c.name"
        " FROM categories c"
        " LEFT JOIN category_groups cg ON cg.id = c.group_id"
        " WHERE c.is_active = 1",
    ).fetchall()
    return {int(r[0]): f"{r[1]}: {r[2]}" if r[1] else str(r[2]) for r in rows}


def _run_rules_pass(
    conn: sqlite3.Connection,
    items: list[ReceiptItemRow],
    store_id: int | None,
) -> tuple[dict[int, tuple[int, int]], list[tuple[int, str]]]:
    """Classify items by rules; return (rule_hits, llm_queue) for remaining items."""
    rule_hits: dict[int, tuple[int, int]] = {}
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
    pool: ProviderPool,
    conn: sqlite3.Connection,
    job: ReceiptJobRow,
    llm_queue: list[tuple[int, str]],
    categories: dict[int, str],
) -> tuple[dict[int, ClassificationResult], bool]:
    """Call LLM for queued items; return (results keyed by item_id, used_failover)."""
    if not llm_queue:
        return {}, False
    normalized_names = [name for _, name in llm_queue]
    results, used_failover = await pool.classify_receipt(
        conn=conn,
        items=normalized_names,
        store_name_raw=job.store_name_raw or "Unknown Store",
        categories=categories,
        ctx=ReceiptContext(receipt_id=job.receipt_id, invoice_number=job.invoice_number),
    )
    return (
        {item_id: result for (item_id, _), result in zip(llm_queue, results, strict=False)},
        used_failover,
    )


def _compute_classifications(
    items: list[ReceiptItemRow],
    rule_hits: dict[int, tuple[int, int]],
    llm_results: dict[int, ClassificationResult],
    total_penalty: int,
) -> dict[int, tuple[int | None, int]]:
    """Merge rule and LLM results into {item_id: (category_id, confidence)}."""
    result: dict[int, tuple[int | None, int]] = {}
    for item in items:
        if item.id in rule_hits:
            result[item.id] = rule_hits[item.id]
        else:
            llm = llm_results.get(item.id)
            if llm is None:
                result[item.id] = (None, 1)
            else:
                conf = max(1, llm.confidence_level - total_penalty)
                result[item.id] = (llm.category_id, conf)
    return result


def _persist_classification_results(
    conn: sqlite3.Connection,
    job: ReceiptJobRow,
    items: list[ReceiptItemRow],
    classifications: dict[int, tuple[int | None, int]],
    store_id: int | None,
) -> None:
    """Write classification results atomically; handles idempotency inside the transaction."""
    by_category: defaultdict[int, list[tuple[ReceiptItemRow, int]]] = defaultdict(list)
    unresolved_items: list[tuple[ReceiptItemRow, int]] = []
    for item in items:
        cat_id, conf = classifications.get(item.id, (None, 1))
        if cat_id is None or conf <= 1:
            unresolved_items.append((item, conf if cat_id is None else 1))
        else:
            by_category[cat_id].append((item, conf))

    receipt_dt_row = conn.execute(
        "SELECT COALESCE(purchase_datetime, created_at) FROM receipts WHERE id = ?",
        [job.receipt_id],
    ).fetchone()
    receipt_dt = (
        receipt_dt_row[0]
        if receipt_dt_row
        else datetime.now(UTC).replace(microsecond=0).isoformat()
    )

    conn.execute("BEGIN IMMEDIATE")
    try:
        if conn.execute(
            "SELECT 1 FROM expenses WHERE receipt_id = ? LIMIT 1",
            [job.receipt_id],
        ).fetchone():
            complete_job(conn, job.receipt_id)
            conn.execute("COMMIT")
            return

        for item, _ in unresolved_items:
            update_receipt_item(
                conn,
                item.id,
                normalize_item_name(item.name_raw),
                ItemClassification(None, 1, None),
            )

        for cat_id, cat_items in by_category.items():
            total = round(sum(i.total_price for i, _ in cat_items), 2)
            min_conf = min(conf for _, conf in cat_items)
            conn.execute(
                """
                INSERT INTO expenses
                       (datetime, amount, amount_original, currency_original,
                        category_id, confidence_level, receipt_id, store_id)
                VALUES (?, ?, ?, 'RSD', ?, ?, ?, ?)
                """,
                [receipt_dt, total, total, cat_id, min_conf, job.receipt_id, store_id],
            )
            expense_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            for item, conf in cat_items:
                norm = normalize_item_name(item.name_raw)
                update_receipt_item(
                    conn,
                    item.id,
                    norm,
                    ItemClassification(cat_id, conf, expense_id),
                )
                if norm and conf >= 2:
                    create_or_update_rule(conn, store_id, norm, RuleSpec(cat_id, conf, "llm"))

        trim_llm_call_log(conn)
        complete_job(conn, job.receipt_id)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


async def _classify_and_persist(
    conn: sqlite3.Connection,
    pool: ProviderPool,
    job: ReceiptJobRow,
    items: list[ReceiptItemRow],
    store_id: int | None,
) -> None:
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
    categories = _load_categories(conn)
    llm_results, used_failover = await _run_llm_pass(pool, conn, job, llm_queue, categories)
    total_penalty = (1 if job.used_journal_fallback else 0) + (1 if used_failover else 0)
    classifications = _compute_classifications(items, rule_hits, llm_results, total_penalty)
    _persist_classification_results(conn, job, items, classifications, store_id)


def _write_fetch_fallback_metadata(
    conn: sqlite3.Connection,
    invoice_number: str,
    reason: str,
) -> None:
    now = datetime.now(UTC).isoformat()
    value = f"{now} | invoice: {invoice_number} | reason: {reason}"
    conn.execute("BEGIN IMMEDIATE")
    try:
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
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
