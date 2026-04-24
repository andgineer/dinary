"""POST /api/expenses endpoint (idempotent on client_expense_id).

Phase 2 API: the PWA sends a 3D payload using catalog primary keys
(``category_id``, optional ``event_id``, list of ``tag_ids``). The
server stores the raw IDs on the ledger; 3D->2D resolution for the
sheet happens lazily in the drain loop from ``sheet_mapping``, so a
change to the ``map`` worksheet retroactively affects unlogged
expenses (but never rewrites already-logged rows).

When ``event_id`` is supplied, any tag names listed on
``events.auto_tags`` are resolved to live tag ids and **unioned** into
the expense's stored ``tag_ids`` — so vacation events auto-tag every
attached expense with ``отпуск`` and ``путешествия`` (routing the
expense into the "путешествия" envelope via the ``map`` tab) without
the PWA having to replicate that logic client-side.

Errors:

* 422 — unknown / inactive ``category_id``, ``event_id``, or any
  ``tag_ids``; FX convert failure; date out of range.
* 409 — ``client_expense_id`` already present with different stored
  body (different amount / date / tag_ids / etc).
"""

import asyncio
import sqlite3
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from dinary.config import settings
from dinary.services import ledger_repo, sheet_mapping
from dinary.services.exchange_rates import get_rate
from dinary.services.sheet_logging import is_sheet_logging_enabled, notify_new_work

router = APIRouter()


class ExpenseRequest(BaseModel):
    client_expense_id: str = Field(min_length=1)
    amount: Decimal = Field(gt=Decimal(0))
    # Defaults to settings.app_currency (the PWA input currency); the
    # server converts to settings.accounting_currency before storing.
    currency: str | None = None
    category_id: int
    event_id: int | None = None
    tag_ids: list[int] = Field(default_factory=list)
    comment: str | None = None
    date: date


class ExpenseResponse(BaseModel):
    status: Literal["ok", "duplicate"]
    month: str
    category_id: int
    amount_original: Decimal
    currency_original: str
    catalog_version: int


@router.post("/api/expenses", response_model=ExpenseResponse)
async def create_expense(req: ExpenseRequest) -> ExpenseResponse:
    # SQLite / NBS calls are synchronous and would otherwise block every
    # other request on the single-worker event loop. Offload the whole
    # body to the default thread pool; the lifespan drain already does
    # the same for its own blocking work.
    resp = await asyncio.to_thread(_create_expense_sync, req)
    # Wake the drain loop for fresh creates so the sheet append runs
    # immediately instead of waiting up to `drain_interval_sec` for the
    # next periodic tick. Duplicates did not enqueue a new job (the
    # queue row was created by the original insert), so skip them.
    if resp.status == "ok" and is_sheet_logging_enabled():
        notify_new_work()
    return resp


def _create_expense_sync(req: ExpenseRequest) -> ExpenseResponse:
    currency = req.currency or settings.app_currency

    con = ledger_repo.get_connection()
    try:
        _resolve_category_for_write(con, req)
        _validate_event(con, req)
        _validate_tags(con, req)

        amount_acc = req.amount
        if currency.upper() != settings.accounting_currency.upper():
            rate = get_rate(
                con,
                req.date,
                currency,
                settings.accounting_currency,
                offline=True,
            )
            amount_acc = (req.amount * rate).quantize(Decimal("0.01"))

        expense_dt = datetime.combine(req.date, datetime.min.time())

        # Union event.auto_tags into the submitted tag set. This is the
        # runtime-write counterpart to the historical importer, which
        # applies the same union at import time; keeping both paths in
        # sync means the "vacation expense always carries both отпуск
        # and путешествия" invariant holds regardless of which path
        # created the row.
        #
        # Idempotency caveat: ``insert_expense`` compares the stored
        # ``tag_ids`` against ``effective_tag_ids`` on ON CONFLICT. If
        # the operator edits ``events.auto_tags`` between the original
        # POST and a replay carrying the same ``client_expense_id`` +
        # ``req.tag_ids``, the recomputed union can differ and the
        # replay gets 409 conflict instead of 200 duplicate. That's a
        # narrow race (the auto_tags edit would have to land between
        # a failed POST and the retry) and the safer of the two
        # failure modes — surfacing it tells the operator their
        # vocabulary changed mid-flight rather than silently writing
        # the older tag set.
        effective_tag_ids: list[int] = list(dict.fromkeys(int(t) for t in req.tag_ids))
        if req.event_id is not None:
            for auto_id in sheet_mapping.resolve_event_auto_tag_ids(con, req.event_id):
                if auto_id not in effective_tag_ids:
                    effective_tag_ids.append(auto_id)

        # ``insert_expense`` is the single source of truth for the
        # duplicate-vs-conflict decision: it runs the INSERT with
        # ``ON CONFLICT DO NOTHING`` and, on conflict, compares the full
        # stored payload against the incoming one. We intentionally do
        # not pre-check with ``lookup_existing_expense`` for the
        # happy-path compare — a double compare would drift over time,
        # and the ON CONFLICT path is already atomic against concurrent
        # POSTs sharing the same ``client_expense_id``.
        amount_acc_f = float(amount_acc)
        amount_orig_f = float(req.amount)
        comment = req.comment or ""
        try:
            result = ledger_repo.insert_expense(
                con,
                client_expense_id=req.client_expense_id,
                expense_datetime=expense_dt,
                amount=amount_acc_f,
                amount_original=amount_orig_f,
                currency_original=currency,
                category_id=req.category_id,
                event_id=req.event_id,
                comment=comment,
                sheet_category=None,
                sheet_group=None,
                tag_ids=effective_tag_ids,
                enqueue_logging=is_sheet_logging_enabled(),
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from None

        if result == "conflict":
            # Re-run the stored-vs-incoming compare so the 409 body
            # names the columns that differ. The common "narrow race"
            # case — the operator edited ``events.auto_tags`` between
            # the original POST and a replay — lands here as a
            # tag_ids-only diff, which lets the client distinguish it
            # from a real body-drift conflict without guessing.
            diff = ledger_repo.describe_expense_conflict(
                con,
                client_expense_id=req.client_expense_id,
                expense_datetime=expense_dt,
                amount=amount_acc_f,
                amount_original=amount_orig_f,
                currency_original=currency,
                category_id=req.category_id,
                event_id=req.event_id,
                comment=comment,
                sheet_category=None,
                sheet_group=None,
                tag_ids=effective_tag_ids,
            )
            detail = (
                f"client_expense_id {req.client_expense_id!r} already exists with different data"
            )
            if diff:
                detail = f"{detail}: {diff}"
            raise HTTPException(status_code=409, detail=detail)

        catalog_version = ledger_repo.get_catalog_version(con)
    finally:
        con.close()

    return ExpenseResponse(
        status="ok" if result == "created" else "duplicate",
        month=req.date.strftime("%Y-%m"),
        category_id=req.category_id,
        amount_original=req.amount,
        currency_original=currency,
        catalog_version=catalog_version,
    )


def _is_replay(
    con: sqlite3.Connection,
    client_expense_id: str,
) -> bool:
    """True if an ``expenses`` row already exists for ``client_expense_id``.

    Used to decide whether inactive catalog references on the incoming
    payload are a real validation error (422) or a replay that should
    be handed off to ``insert_expense``'s ``ON CONFLICT`` compare path
    for correct duplicate-vs-conflict classification (200 or 409).

    The actual duplicate-vs-conflict decision is made in a single place
    (``ledger_repo.insert_expense``) so we never drift two compare
    implementations apart.

    Race window (single-worker deployment, very narrow):

    This ``SELECT`` runs before the ``BEGIN`` that ``insert_expense``
    opens, so two concurrent ``POST /api/expenses`` calls with the
    same ``client_expense_id`` can both see "no existing row" here.
    For requests whose bodies exactly match, that's harmless — the
    UNIQUE constraint on ``client_expense_id`` still serialises them
    into one INSERT + one duplicate inside ``insert_expense``. The
    only degraded case is when the *losing* request also happens to
    reference an inactive catalog item: it will be rejected with 422
    "Inactive ..." down-stream at ``_validate_*`` instead of being
    classified as a 200 duplicate (matching body) or 409 conflict
    (mismatching body) by ``insert_expense``'s compare path. The
    client retries and converges because its next attempt sees the
    committed row and the validation defers correctly.

    Acceptable because: (1) single-worker uvicorn deployment means
    this race requires two in-flight calls from the same PWA within
    a few milliseconds, which the client never produces (it queues
    sequentially through the flush loop); (2) the mis-classified
    response is still safe — the server has not persisted anything
    wrong, it has only returned 422 where 200/409 would be more
    accurate. A tighter fix would move the existence check inside
    ``insert_expense``'s ``BEGIN``; deferred because the refactor
    touches every catalog validation path in this module.
    """
    return ledger_repo.lookup_existing_expense(client_expense_id, con=con) is not None


def _resolve_category_for_write(
    con: sqlite3.Connection,
    req: ExpenseRequest,
) -> None:
    """Validate ``req.category_id`` resolves to an active category.

    Known-but-inactive is only accepted as an idempotent PWA replay
    (``client_expense_id`` already present on ``expenses``). Whether
    the stored body actually matches is ``insert_expense``'s job: if
    the payload's ``category_id`` differs from the stored one, the
    ``ON CONFLICT`` compare path surfaces a 409; if it matches, we
    get a 200 duplicate. Either outcome is more precise than the old
    "blanket 422 on any inactive id" behaviour.
    """
    row = con.execute(
        "SELECT id, is_active FROM categories WHERE id = ?",
        [req.category_id],
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown category_id: {req.category_id}",
        )
    if bool(row[1]):
        return
    if _is_replay(con, req.client_expense_id):
        return
    raise HTTPException(
        status_code=422,
        detail=f"Inactive category_id: {req.category_id}",
    )


def _validate_event(
    con: sqlite3.Connection,
    req: ExpenseRequest,
) -> None:
    if req.event_id is None:
        return
    row = con.execute(
        "SELECT is_active FROM events WHERE id = ?",
        [req.event_id],
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown event_id: {req.event_id}",
        )
    if bool(row[0]):
        return
    # Same idempotent-replay carve-out as categories: defer the final
    # classification to ``insert_expense``'s ON CONFLICT compare path
    # so a replay with a *different* inactive event_id returns 409
    # instead of 422.
    if _is_replay(con, req.client_expense_id):
        return
    raise HTTPException(
        status_code=422,
        detail=f"Inactive event_id: {req.event_id}",
    )


def _validate_tags(
    con: sqlite3.Connection,
    req: ExpenseRequest,
) -> None:
    if not req.tag_ids:
        return
    unique_ids = list({int(t) for t in req.tag_ids})
    # S608 false positive: ``placeholders`` is a dynamic-length string of
    # literal ``?`` characters whose count is bounded by ``len(unique_ids)``.
    # No user-supplied text is interpolated into the SQL string.
    placeholders = ",".join(["?"] * len(unique_ids))
    rows = con.execute(
        f"SELECT id, is_active FROM tags WHERE id IN ({placeholders})",  # noqa: S608
        unique_ids,
    ).fetchall()
    found = {int(r[0]): bool(r[1]) for r in rows}
    missing = [t for t in unique_ids if t not in found]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown tag_ids: {missing}",
        )
    inactive = [t for t in unique_ids if not found[t]]
    if not inactive:
        return
    # Replay carve-out: defer to ``insert_expense`` compare. If the
    # stored row's tag set matches, we return 200 duplicate; if it
    # differs (including a tag-set mismatch on otherwise-same
    # payload), the compare path surfaces a 409. This is strictly
    # more informative than the previous "422 on any inactive tag"
    # behaviour and keeps the single source of truth for
    # duplicate-vs-conflict inside ``insert_expense``.
    if _is_replay(con, req.client_expense_id):
        return
    raise HTTPException(
        status_code=422,
        detail=f"Inactive tag_ids: {inactive}",
    )
