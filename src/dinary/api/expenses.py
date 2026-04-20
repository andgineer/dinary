"""POST /api/expenses endpoint (idempotent on client_expense_id).

Phase 2 API: the PWA sends a 3D payload using catalog primary keys
(``category_id``, optional ``event_id``, list of ``tag_ids``). The
server stores the raw IDs on the ledger; 3D->2D resolution for the
sheet happens lazily in the drain loop from ``runtime_mapping``, so a
change to the ``map`` worksheet retroactively affects unlogged
expenses (but never rewrites already-logged rows).

Errors:

* 422 — unknown / inactive ``category_id``, ``event_id``, or any
  ``tag_ids``; FX convert failure; date out of range.
* 409 — ``client_expense_id`` already present with different stored
  body (different amount / date / tag_ids / etc).
"""

import asyncio
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

import duckdb
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from dinary.config import settings
from dinary.services import duckdb_repo
from dinary.services.nbs import convert
from dinary.services.sheet_logging import is_sheet_logging_enabled, notify_new_work

router = APIRouter()


class ExpenseRequest(BaseModel):
    client_expense_id: str = Field(min_length=1)
    amount: Decimal = Field(gt=Decimal(0))
    currency: str | None = None  # defaults to settings.app_currency
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
    # DuckDB / NBS calls are synchronous and would otherwise block every
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

    con = duckdb_repo.get_connection()
    try:
        _resolve_category_for_write(con, req)
        _validate_event(con, req)
        _validate_tags(con, req)

        amount_app = req.amount
        if currency.upper() != settings.app_currency.upper():
            amount_app, _rate = convert(
                con,
                req.amount,
                currency,
                settings.app_currency,
                req.date,
            )

        expense_dt = datetime.combine(req.date, datetime.min.time())

        # ``insert_expense`` is the single source of truth for the
        # duplicate-vs-conflict decision: it runs the INSERT with
        # ``ON CONFLICT DO NOTHING`` and, on conflict, compares the full
        # stored payload against the incoming one. We intentionally do
        # not pre-check with ``lookup_existing_expense`` for the
        # happy-path compare — a double compare would drift over time,
        # and the ON CONFLICT path is already atomic against concurrent
        # POSTs sharing the same ``client_expense_id``.
        try:
            result = duckdb_repo.insert_expense(
                con,
                client_expense_id=req.client_expense_id,
                expense_datetime=expense_dt,
                amount=float(amount_app),
                amount_original=float(req.amount),
                currency_original=currency,
                category_id=req.category_id,
                event_id=req.event_id,
                comment=req.comment or "",
                sheet_category=None,
                sheet_group=None,
                tag_ids=list(req.tag_ids),
                enqueue_logging=is_sheet_logging_enabled(),
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from None

        if result == "conflict":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"client_expense_id {req.client_expense_id!r} already exists "
                    "with different data"
                ),
            )

        catalog_version = duckdb_repo.get_catalog_version(con)
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
    con: duckdb.DuckDBPyConnection,
    client_expense_id: str,
) -> bool:
    """True if an ``expenses`` row already exists for ``client_expense_id``.

    Used to decide whether inactive catalog references on the incoming
    payload are a real validation error (422) or a replay that should
    be handed off to ``insert_expense``'s ``ON CONFLICT`` compare path
    for correct duplicate-vs-conflict classification (200 or 409).

    The actual duplicate-vs-conflict decision is made in a single place
    (``duckdb_repo.insert_expense``) so we never drift two compare
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
    return duckdb_repo.lookup_existing_expense(client_expense_id, con=con) is not None


def _resolve_category_for_write(
    con: duckdb.DuckDBPyConnection,
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
    con: duckdb.DuckDBPyConnection,
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
    con: duckdb.DuckDBPyConnection,
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
