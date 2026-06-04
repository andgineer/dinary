"""Analytics API: GET /api/analytics/summary"""

import sqlite3
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends

from dinary.config import settings
from dinary.db.storage import get_db

router = APIRouter()

_SQL_DIR = Path(__file__).resolve().parent.parent / "db" / "sql"
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


@router.get("/api/analytics/summary")
def get_analytics_summary(con: sqlite3.Connection = Depends(get_db)) -> dict:  # noqa: B008
    cur = con.cursor()
    currency = settings.accounting_currency

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
    }
