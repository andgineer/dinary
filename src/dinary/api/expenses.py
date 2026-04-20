"""POST /api/expenses endpoint (idempotent on `(date.year, expense_id)`)."""

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from dinary.services import duckdb_repo
from dinary.services.nbs import convert_to_eur
from dinary.services.sheet_logging import is_sheet_logging_enabled, schedule_logging

logger = logging.getLogger(__name__)
router = APIRouter()


class ExpenseRequest(BaseModel):
    """Phase-1 request body. Tags and event are intentionally absent — the
    PWA has no way to set them yet, and the optional sheet-logging worker
    falls back to ``category.name`` when ``logging_mapping`` has no row
    for the (category, event=NULL, tags=[]) triple.
    """

    expense_id: str = Field(min_length=1)
    amount: float = Field(gt=0)
    currency: str = Field(default="RSD", pattern="^(RSD|EUR)$")
    category: str
    comment: str = ""
    date: date


class ExpenseResponse(BaseModel):
    """Phase-1 response. `amount_original`/`currency_original` echo the input
    pair (the field name `amount_rsd` from the prior contract was misleading
    when `currency=EUR`)."""

    status: Literal["created", "duplicate"]
    expense_id: str
    month: str
    category: str
    amount_original: float
    currency_original: str
    catalog_version: int


@router.post("/api/expenses", response_model=ExpenseResponse)
async def create_expense(req: ExpenseRequest) -> ExpenseResponse:  # noqa: PLR0912, PLR0915
    """Insert one expense and (optionally) queue it for sheet logging.

    The PLR0912/PLR0915 lints are silenced deliberately: the body is a
    flat sequence of HTTP-error gates (cross-year reservation check,
    catalog-version validation, duplicate detection, conflict mapping,
    insert, schedule_logging) and each branch maps 1:1 to a documented
    HTTP status code. Decomposing the gates into helpers would force
    the helpers to either return rich `(status, detail)` tuples or
    raise `HTTPException` themselves — both options scatter the API
    contract across modules without making any single piece simpler.

    Idempotency:
      * If `expense_id` already exists in `budget_<year>.duckdb` and every
        field matches, return 200 `duplicate` without re-queuing logging.
      * If it exists with mismatching fields, return 409.
      * If `expense_id` is registered in another year (cross-year reuse),
        return 409.

    Sheet logging is opt-in via ``DINARY_SHEET_LOGGING_SPREADSHEET``; when
    unset, ``enqueue_logging`` is False so jobs do not accumulate. When
    enabled, the worker uses ``logging_mapping`` (year-agnostic) to pick
    ``(sheet_category, sheet_group)``, falling back to ``category.name``
    if the mapping is missing.
    """
    year = req.date.year

    config_con = duckdb_repo.get_config_connection(read_only=True)
    try:
        category_row = config_con.execute(
            "SELECT id FROM categories WHERE name = ?",
            [req.category],
        ).fetchone()
        if category_row is None:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown category: {req.category!r}",
            )
        category_id = int(category_row[0])
    finally:
        config_con.close()

    registered_year = duckdb_repo.get_registered_expense_year(req.expense_id)
    if registered_year is not None and registered_year != year:
        raise HTTPException(
            status_code=409,
            detail=(
                f"expense_id {req.expense_id!r} already exists in year {registered_year}; "
                "cross-year reuse is rejected."
            ),
        )

    config_con = duckdb_repo.get_config_connection(read_only=False)
    try:
        rate_date = req.date.replace(day=1)
        amount_eur = float(
            convert_to_eur(
                config_con,
                Decimal(str(req.amount)),
                req.currency,
                rate_date,
            ),
        )
    finally:
        config_con.close()

    expense_dt = datetime.combine(req.date, datetime.min.time())

    # Reserve the expense_id in the global registry before the row insert so a
    # subsequent retry for a different year sees the conflict immediately.
    # `we_own_reservation` lets us release on any failure path so a phantom
    # registry row doesn't outlive the failed insert.
    stored_year, we_own_reservation = duckdb_repo.reserve_expense_id_year(
        req.expense_id,
        year,
    )

    # Bug K1: the read-only `get_registered_expense_year` check above is racy
    # by design — between that probe and `reserve_expense_id_year`, a peer
    # POST for the same `expense_id` against a *different* year can win the
    # registry insert. `reserve_expense_id_year` returns the canonical
    # `stored_year` exactly so the loser can detect this and 409 instead of
    # writing into the wrong yearly budget DB. Skipping this check would
    # leave the registry pointing at year A while `budget_B.duckdb` quietly
    # gained a row for the same expense_id — exactly the divergence the
    # registry exists to prevent.
    if stored_year != year:
        # `we_own_reservation` is False on this path (the row was already
        # there for `stored_year`), so no cleanup is needed — leaving the
        # winner's registry row intact is the correct outcome.
        raise HTTPException(
            status_code=409,
            detail=(
                f"expense_id {req.expense_id!r} already exists in year {stored_year}; "
                "cross-year reuse is rejected."
            ),
        )

    # `release_registry` controls the cleanup-on-failure path. We only set it
    # to True when the budget insert provably did NOT take ownership of the
    # expense row — otherwise a release would re-open the cross-year reuse
    # hole the registry exists to close.
    #
    # Why "conflict" must NOT release: the conflict result proves the budget
    # DB already holds this expense_id for `year` (with mismatched fields).
    # If `we_own_reservation` is True here it means `config.duckdb` was wiped
    # (e.g. by `inv import-catalog`) while the budget DB kept its rows; the
    # reservation we just inserted is the correct repair, not a phantom row.
    # Releasing it would let a subsequent POST with `date.year=Y+1` succeed
    # against `budget_(Y+1)` and create a duplicate `expense_id` across years.
    release_registry = False
    try:
        con = duckdb_repo.get_budget_connection(year)
        try:
            try:
                result = duckdb_repo.insert_expense(
                    con=con,
                    expense_id=req.expense_id,
                    expense_datetime=expense_dt,
                    amount=amount_eur,
                    amount_original=req.amount,
                    currency_original=req.currency,
                    category_id=category_id,
                    event_id=None,
                    comment=req.comment,
                    sheet_category=None,
                    sheet_group=None,
                    tag_ids=[],
                    enqueue_logging=is_sheet_logging_enabled(),
                )
            except ValueError as e:
                # The insert itself failed (e.g. unknown category_id) so no
                # row was committed. Release any reservation we made.
                release_registry = True
                raise HTTPException(status_code=422, detail=str(e)) from None
            except Exception:
                # Any other DB error: be conservative and release so a retry
                # can succeed. If the row actually committed before the error,
                # the next POST will see "duplicate"/"conflict" and re-establish
                # the registry row.
                release_registry = True
                raise
        finally:
            con.close()

        if result == "conflict":
            # Budget row exists for `year` with different fields → the
            # registry correctly points at `year`; do NOT release.
            raise HTTPException(
                status_code=409,
                detail=f"Expense {req.expense_id} already exists with different data",
            )
    finally:
        if release_registry and we_own_reservation:
            try:
                duckdb_repo.release_expense_id_year(req.expense_id)
            except Exception:
                logger.exception(
                    "Failed to release leaked registry reservation for %s",
                    req.expense_id,
                )

    if result == "created":
        schedule_logging(req.expense_id, year)

    # Read catalog_version AFTER the insert so the response reflects the
    # schema the row was written against. Reading it at the top of the
    # handler races with `inv import-catalog`: a concurrent rebuild would
    # bump the version mid-request, the insert would land against the new
    # schema, and the client would still cache the old version — exactly
    # the inverse of what catalog_version exists to guarantee.
    config_con = duckdb_repo.get_config_connection(read_only=True)
    try:
        catalog_version = duckdb_repo.get_catalog_version(config_con)
    finally:
        config_con.close()

    month_label = req.date.strftime("%Y-%m")
    return ExpenseResponse(
        status=result,
        expense_id=req.expense_id,
        month=month_label,
        category=req.category,
        amount_original=req.amount,
        currency_original=req.currency,
        catalog_version=catalog_version,
    )
