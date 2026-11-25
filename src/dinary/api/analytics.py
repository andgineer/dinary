"""Analytics API: summary, per-event detail and DB snapshot endpoints."""

import sqlite3
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from dinary.config import settings
from dinary.db.receipts import classification_job_counts
from dinary.db.storage import get_connection, get_db

router = APIRouter()

_SQL_DIR = Path(__file__).resolve().parent.parent / "db" / "sql"
_EVENT_DAYS_LIMIT = 7
_TREND_BLOCK_KEEP = 4
_MINUS = "\u2212"
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _sql(name: str) -> str:
    return (_SQL_DIR / name).read_text()


def _fmt(amount: float) -> str:
    return f"{round(amount):,}".replace(",", " ")


def _fmt_signed(amount: float) -> str:
    rounded = round(amount)
    if rounded > 0:
        return f"+{_fmt(rounded)}"
    if rounded < 0:
        return f"{_MINUS}{_fmt(-rounded)}"
    return "0"


def _fmt_pct(pct_change: float | None) -> str | None:
    return None if pct_change is None else f"{abs(int(pct_change))}%"


def _fmt_date_range(date_from: str | date, date_to: str | date) -> str:
    df = date.fromisoformat(str(date_from)[:10]) if not isinstance(date_from, date) else date_from
    dt = date.fromisoformat(str(date_to)[:10]) if not isinstance(date_to, date) else date_to
    if df.year == dt.year and df.month == dt.month:
        return f"{df.day}–{dt.day} {_MONTHS[df.month - 1]} {df.year}"
    if df.year == dt.year:
        return f"{df.day} {_MONTHS[df.month - 1]}–{dt.day} {_MONTHS[dt.month - 1]} {df.year}"
    return f"{df.day} {_MONTHS[df.month - 1]} {df.year}–{dt.day} {_MONTHS[dt.month - 1]} {dt.year}"


def _fmt_day(day: str) -> str:
    d = date.fromisoformat(day)
    return f"{d.day} {_MONTHS[d.month - 1]}"


def _event_categories(
    cur: sqlite3.Cursor,
    event_id: int,
    total: float,
    currency: str,
) -> list[dict]:
    return [
        {
            "category_id": r[0],
            "category_name": r[1],
            "group_name": r[2],
            "total": _fmt(r[3]),
            "share": r[3] / total if total else 0.0,
            "currency": currency,
        }
        for r in cur.execute(_sql("analytics_event_categories.sql"), (event_id,)).fetchall()
    ]


def _event_days(cur: sqlite3.Cursor, event_id: int, currency: str) -> list[dict]:
    return [
        {"date": r[0], "date_label": _fmt_day(r[0]), "total": _fmt(r[1]), "currency": currency}
        for r in cur.execute(
            _sql("analytics_event_days.sql"),
            (event_id, _EVENT_DAYS_LIMIT),
        ).fetchall()
    ]


@dataclass(frozen=True)
class _Driver:
    key: int | str | None
    label: str | None
    sublabel: str | None
    recent: float
    prior: float
    count: int | None = None

    @property
    def delta(self) -> float:
        return self.recent - self.prior


def _fetch_drivers(
    cur: sqlite3.Cursor,
    sql_name: str,
    kind: str,
    basket_id: int,
) -> list[_Driver]:
    params = {"kind": kind, "basket_id": basket_id}
    return [_Driver(*r) for r in cur.execute(_sql(sql_name), params).fetchall()]


def _fold_drivers(rows: list[_Driver], keep: int = _TREND_BLOCK_KEEP) -> list[_Driver]:
    # Folding a single row would only rename it, so one extra row is shown as is.
    if len(rows) <= keep + 1:
        return rows
    rest = rows[keep:]
    other = _Driver(
        key=None,
        label=None,
        sublabel=None,
        recent=sum(r.recent for r in rest),
        prior=sum(r.prior for r in rest),
        count=len(rest),
    )
    return [*rows[:keep], other]


def _fold_event_drivers(rows: list[_Driver]) -> list[_Driver]:
    named = _fold_drivers([r for r in rows if r.key is not None])
    other = [r for r in named if r.count is not None]
    shown = [r for r in named if r.count is None] + [r for r in rows if r.key is None]
    return [*sorted(shown, key=lambda r: abs(r.delta), reverse=True), *other]


def _driver_row(row: _Driver, max_abs_delta: float) -> dict:
    return {
        "key": row.key,
        "label": row.label,
        "sublabel": row.sublabel,
        "recent": _fmt(row.recent),
        "prior": _fmt(row.prior),
        "delta": _fmt_signed(row.delta),
        "direction": "up" if row.recent >= row.prior else "down",
        "share": abs(row.delta) / max_abs_delta if max_abs_delta else 0.0,
        "count": row.count,
    }


def _trend_block(
    dimension: str,
    rows: list[_Driver],
    *,
    receipt_backed_only: bool = False,
    fold: Callable[[list[_Driver]], list[_Driver]] = _fold_drivers,
) -> dict:
    recent = sum(r.recent for r in rows)
    prior = sum(r.prior for r in rows)
    folded = fold(rows)
    max_abs_delta = max((abs(r.delta) for r in folded), default=0.0)
    return {
        "dimension": dimension,
        "receipt_backed_only": receipt_backed_only,
        "recent": _fmt(recent),
        "prior": _fmt(prior),
        "delta": _fmt_signed(recent - prior),
        "rows": [_driver_row(r, max_abs_delta) for r in folded],
    }


def _largest_explaining(candidates: list[_Driver], basket_delta: float) -> _Driver | None:
    if not candidates:
        return None
    largest = max(candidates, key=lambda r: abs(r.delta))
    return largest if abs(largest.delta) * 2 >= abs(basket_delta) else None


def _same_sign(rows: list[_Driver], basket_delta: float) -> list[_Driver]:
    return [r for r in rows if r.delta * basket_delta > 0]


def _is_single_category_group(cur: sqlite3.Cursor, kind: str, basket_id: int) -> bool:
    if kind != "group":
        return False
    params = {"basket_id": basket_id}
    return cur.execute(_sql("analytics_trend_group_size.sql"), params).fetchone()[0] == 1


def _trend_reason(
    single_category_group: bool,
    basket_delta: float,
    categories: list[_Driver],
    events: list[_Driver],
) -> dict | None:
    named_events = [r for r in events if r.key is not None]
    event = _largest_explaining(_same_sign(named_events, basket_delta), basket_delta)
    if event is not None:
        return {"kind": "event", "names": [event.label]}
    # A group spent through one category would only be restated by naming that category.
    if single_category_group:
        return None
    candidates = _same_sign(categories, basket_delta)
    category = _largest_explaining(candidates, basket_delta)
    if category is not None:
        return {"kind": "category", "names": [category.label]}
    if not candidates:
        return None
    top = sorted(candidates, key=lambda r: abs(r.delta), reverse=True)[:2]
    return {"kind": "category", "names": [r.label for r in top]}


def _trend_blocks(
    cur: sqlite3.Cursor,
    kind: str,
    basket_id: int,
    receipts_in_both_windows: bool,
) -> list[dict]:
    blocks = [
        _trend_block(
            "category",
            _fetch_drivers(cur, "analytics_trend_categories.sql", kind, basket_id),
        ),
    ]
    events = _fetch_drivers(cur, "analytics_trend_events.sql", kind, basket_id)
    if any(r.key is not None for r in events):
        blocks.append(_trend_block("event", events, fold=_fold_event_drivers))
    if receipts_in_both_windows:
        for dimension, sql_name in (
            ("chain", "analytics_trend_chains.sql"),
            ("item", "analytics_trend_items.sql"),
        ):
            rows = _fetch_drivers(cur, sql_name, kind, basket_id)
            blocks.append(_trend_block(dimension, rows, receipt_backed_only=True))
    return blocks


@router.get("/api/analytics/summary")
def get_analytics_summary(con: sqlite3.Connection = Depends(get_db)) -> dict:  # noqa: B008
    cur = con.cursor()
    currency = settings.accounting_currency
    # Read before the aggregates: a job committing mid-handler must not yield stale totals
    # alongside an empty queue, which would let the client treat them as fresh.
    receipts_queue = classification_job_counts(con)

    this_month, last_month, ytd_expenses = cur.execute(_sql("analytics_summary.sql")).fetchone()
    ytd_income = cur.execute(_sql("analytics_ytd_income.sql")).fetchone()[0]

    ytd_savings = ytd_income - ytd_expenses
    savings_rate = round(ytd_savings * 100 / ytd_income) if ytd_income > 0 else 0

    events = [
        {
            "id": r[0],
            "name": r[1],
            "date_range": _fmt_date_range(r[2], r[3]),
            "total": _fmt(r[4]),
            "currency": currency,
            "open": bool(r[5]),
        }
        for r in cur.execute(_sql("analytics_events.sql")).fetchall()
    ]

    trend_rows = cur.execute(_sql("analytics_auto_trends.sql")).fetchall()
    trends = [
        {
            "kind": r[0],
            "id": r[1],
            "basket_name": r[2],
            "direction": r[6],
            "pct": _fmt_pct(r[5]),
            "reason": _trend_reason(
                _is_single_category_group(cur, r[0], r[1]),
                r[3] - r[4],
                _fetch_drivers(cur, "analytics_trend_categories.sql", r[0], r[1]),
                _fetch_drivers(cur, "analytics_trend_events.sql", r[0], r[1]),
            ),
        }
        for r in trend_rows
    ] or None

    return {
        "summary": {
            "this_month_total": _fmt(this_month),
            "last_month_total": _fmt(last_month),
            "ytd_total": _fmt(ytd_expenses),
            "ytd_savings": _fmt(ytd_savings),
            "savings_rate": f"{savings_rate}%",
            "currency": currency,
        },
        "events": events,
        "trends": trends,
        "receipts_queue": receipts_queue,
    }


@router.get("/api/analytics/events/{event_id}")
def get_analytics_event(event_id: int, con: sqlite3.Connection = Depends(get_db)) -> dict:  # noqa: B008
    cur = con.cursor()
    currency = settings.accounting_currency
    row = cur.execute(_sql("analytics_event.sql"), (event_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Event not found")
    total = row[4]
    return {
        "id": row[0],
        "name": row[1],
        "date_range": _fmt_date_range(row[2], row[3]),
        "total": _fmt(total),
        "currency": currency,
        "open": bool(row[5]),
        "categories": _event_categories(cur, event_id, total, currency),
        "days": _event_days(cur, event_id, currency),
    }


@router.get("/api/analytics/trends/{kind}/{basket_id}")
def get_analytics_trend(
    kind: Literal["group", "tag"],
    basket_id: int,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> dict:
    cur = con.cursor()
    params = {"kind": kind, "basket_id": basket_id}
    row = cur.execute(_sql("analytics_trend_header.sql"), params).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Basket not found")
    name, recent, prior, receipt_recent, receipt_prior, pct_change, direction = row
    receipts_in_both_windows = receipt_recent > 0 and receipt_prior > 0
    return {
        "kind": kind,
        "id": basket_id,
        "name": name,
        "direction": direction,
        "pct": _fmt_pct(pct_change),
        "currency": settings.accounting_currency,
        "recent": _fmt(recent),
        "prior": _fmt(prior),
        "delta": _fmt_signed(recent - prior),
        "blocks": _trend_blocks(cur, kind, basket_id, receipts_in_both_windows),
    }


@router.get("/api/analytics/db-snapshot")
def get_db_snapshot() -> FileResponse:
    """Return a consistent point-in-time copy of the live ledger as a SQLite file."""
    source = get_connection()
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        try:
            target = sqlite3.connect(tmp_path)
            try:
                source.backup(target)
            finally:
                target.close()
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
    finally:
        source.close()
    return FileResponse(
        tmp_path,
        media_type="application/octet-stream",
        filename="dinary-snapshot.db",
        background=BackgroundTask(lambda: tmp_path.unlink(missing_ok=True)),
    )
