"""Loading of analytics view ("basket") data frames from the ledger replica.
Composes LMDB-backed view configs with DuckDB queries so dashboard cells stay
thin — one helper call instead of wiring IO dependencies each."""

import json
from pathlib import Path

import polars as pl

from dinary_analytics.connection import load_query, open_ledger
from dinary_analytics.settings import get_view, list_view_ids

_VIEW_DATA_SCHEMA = {
    "basket_name": pl.String,
    "year_month": pl.String,
    "group_name": pl.String,
    "total_amount": pl.Float64,
}


def empty_view_frame() -> pl.DataFrame:
    """Return an empty view-data frame with the canonical schema."""
    return pl.DataFrame(
        {"basket_name": [], "year_month": [], "group_name": [], "total_amount": []},
        schema=_VIEW_DATA_SCHEMA,
    )


def load_view_frame(
    config: dict,
    date_from: str,
    replica_path: Path | None = None,
) -> pl.DataFrame:
    """Run view_data.sql for one view config and return its aggregated frame."""
    sql = load_query("view_data")
    con = open_ledger(replica_path=replica_path)
    try:
        rows = con.execute(sql, [json.dumps(config), date_from]).fetchall()
    finally:
        con.close()
    return pl.DataFrame(
        {
            "basket_name": [r[0] for r in rows],
            "year_month": [r[1] for r in rows],
            "group_name": [r[2] for r in rows],
            "total_amount": [float(r[3]) for r in rows],
        },
        schema=_VIEW_DATA_SCHEMA,
    )


def load_pinned_view_frames(
    date_from: str,
    replica_path: Path | None = None,
    db_path: Path | None = None,
) -> list[tuple[str, dict, pl.DataFrame]]:
    """Return (view_id, config, frame) for every pinned view, in storage order."""
    out: list[tuple[str, dict, pl.DataFrame]] = []
    for view_id in list_view_ids(db_path=db_path):
        config = get_view(view_id, db_path=db_path)
        if not config:
            continue
        out.append((view_id, config, load_view_frame(config, date_from, replica_path)))
    return out
