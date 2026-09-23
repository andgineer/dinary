"""Analytics API: summary, per-event detail and DB snapshot endpoints."""

import sqlite3
import tempfile
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from dinary.config import settings
from dinary.db.receipts import classification_job_counts
from dinary.db.storage import get_connection, get_db

router = APIRouter()

_SQL_DIR = Path(__file__).resolve().parent.parent / "db" / "sql"
_EVENT_DAYS_LIMIT = 7
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _sql(name: str) -> str:
    return (_SQL_DIR / name).read_text()


def _fmt(amount: float) -> str:
    return f"{round(amount):,}".replace(",", " ")


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
            "basket_name": r[1],
            "direction": r[5],
            "pct": f"{abs(int(r[4]))}%",
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
