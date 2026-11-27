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


def duplicate_basket_name(baskets: list[dict], default_basket: str) -> str | None:
    """Return a name shared by two baskets, or by a basket and the default basket.

    The chart merges baskets by name, so a repeated name shows one bar that no single
    basket's triggers describe.
    """
    seen: set[str | None] = {default_basket}
    for basket in baskets:
        name = basket.get("name")
        if name in seen:
            return name
        seen.add(name)
    return None


def drill_down_view(
    parent: dict,
    basket_name: str,
    baskets: list[dict],
    default_basket: str,
    name: str = "Breakdown",
) -> dict:
    """Return a view that splits one basket of ``parent`` into ``baskets``.

    The scope composes with the parent's own scope, so a drill-down can nest. An
    expense the parent assigned to an earlier basket stays out of the drilled one,
    and drilling into the default basket keeps only what none of the parent's
    baskets matched. Raises ValueError for a basket the parent does not have.
    """
    parent_baskets = parent.get("baskets") or []
    parent_scope = parent.get("scope") or {}
    required = list(parent_scope.get("required") or [])
    excluded = list(parent_scope.get("excluded") or [])
    duplicate = duplicate_basket_name(parent_baskets, parent.get("default_basket", ""))
    if duplicate is not None:
        raise ValueError(f"The view uses the basket name {duplicate!r} more than once.")
    names = [b.get("name") for b in parent_baskets]
    if basket_name in names:
        position = names.index(basket_name)
        required.append(parent_baskets[position].get("triggers") or {})
        excluded.extend(b.get("triggers") or {} for b in parent_baskets[:position])
    elif basket_name == parent.get("default_basket"):
        excluded.extend(b.get("triggers") or {} for b in parent_baskets)
    else:
        raise ValueError(f"The view has no basket named {basket_name!r}.")
    scope_name = (
        f"{parent_scope['name']} ▸ {basket_name}" if parent_scope.get("name") else basket_name
    )
    return {
        "name": name,
        "baskets": baskets,
        "default_basket": default_basket,
        "chart_type": parent.get("chart_type", "stacked_bar_monthly"),
        "scope": {"name": scope_name, "required": required, "excluded": excluded},
    }


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
