"""Thin helpers for mapping Ibis query results to typed dataclass instances."""

from __future__ import annotations

import dataclasses
from typing import TypeVar

import pandas as pd
import ibis.expr.types as ir

T = TypeVar("T")


def _sanitize(val):
    """Convert pandas NaN/NaT to Python None for clean dataclass construction."""
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except (ValueError, TypeError):
        pass
    return val


def _sanitize_row(record: dict, fields: set[str]) -> dict:
    return {k: _sanitize(v) for k, v in record.items() if k in fields}


def fetchone_as(cls: type[T], expr: ir.Table) -> T | None:
    """Execute an Ibis table expression and map the first row to a dataclass."""
    df = expr.limit(1).execute()
    if df.empty:
        return None
    fields = {f.name for f in dataclasses.fields(cls)}
    record = df.iloc[0].to_dict()
    return cls(**_sanitize_row(record, fields))


def fetchall_as(cls: type[T], expr: ir.Table) -> list[T]:
    """Execute an Ibis table expression and map all rows to dataclass instances."""
    df = expr.execute()
    if df.empty:
        return []
    fields = {f.name for f in dataclasses.fields(cls)}
    return [cls(**_sanitize_row(rec, fields)) for rec in df.to_dict(orient="records")]
