"""LLM provider business logic."""

import sqlite3
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel

from dinary.db.storage import transaction


class ProviderIn(BaseModel):
    label: str
    base_url: str
    api_key: str
    model: str
    is_enabled: bool = True


class ProviderPatch(BaseModel):
    label: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    is_enabled: bool | None = None


def _row_to_dict(row: tuple) -> dict[str, Any]:
    return {
        "id": row[0],
        "label": row[1],
        "base_url": row[2],
        "model": row[3],
        "is_enabled": bool(row[4]),
        "rate_limited_until": row[5],
        "created_at": row[6],
    }


def list_providers(con: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT id, label, base_url, model, is_enabled,"
        "       rate_limited_until, created_at"
        " FROM llmbroker_providers ORDER BY id",
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def add_provider(body: ProviderIn, con: sqlite3.Connection) -> dict[str, Any]:
    with transaction(con):
        con.execute(
            "INSERT INTO llmbroker_providers"
            " (label, base_url, api_key, model, is_enabled)"
            " VALUES (?, ?, ?, ?, ?)",
            [
                body.label,
                body.base_url,
                body.api_key,
                body.model,
                1 if body.is_enabled else 0,
            ],
        )
        provider_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    return {"id": int(provider_id)}


def update_provider(
    provider_id: int,
    body: ProviderPatch,
    con: sqlite3.Connection,
) -> dict[str, Any]:
    if (
        con.execute(
            "SELECT id FROM llmbroker_providers WHERE id = ?",
            [provider_id],
        ).fetchone()
        is None
    ):
        raise HTTPException(status_code=404, detail="Provider not found")
    updates: list[str] = []
    params: list[Any] = []
    if body.label is not None:
        updates.append("label = ?")
        params.append(body.label)
    if body.base_url is not None:
        updates.append("base_url = ?")
        params.append(body.base_url)
    if body.api_key is not None:
        updates.append("api_key = ?")
        params.append(body.api_key)
    if body.model is not None:
        updates.append("model = ?")
        params.append(body.model)
    if body.is_enabled is not None:
        updates.append("is_enabled = ?")
        params.append(1 if body.is_enabled else 0)
    if updates:
        with transaction(con):
            params.append(provider_id)
            con.execute(
                f"UPDATE llmbroker_providers SET {', '.join(updates)} WHERE id = ?",  # noqa: S608
                params,
            )
    return {"status": "ok"}


def delete_provider(provider_id: int, con: sqlite3.Connection) -> dict[str, Any]:
    if (
        con.execute(
            "SELECT id FROM llmbroker_providers WHERE id = ?",
            [provider_id],
        ).fetchone()
        is None
    ):
        raise HTTPException(status_code=404, detail="Provider not found")
    conflict = False
    with transaction(con):
        enabled_count = con.execute(
            "SELECT COUNT(*) FROM llmbroker_providers WHERE is_enabled = 1",
        ).fetchone()[0]
        is_enabled = con.execute(
            "SELECT is_enabled FROM llmbroker_providers WHERE id = ?",
            [provider_id],
        ).fetchone()[0]
        if enabled_count <= 1 and is_enabled:
            conflict = True
        else:
            con.execute("DELETE FROM llmbroker_providers WHERE id = ?", [provider_id])
    if conflict:
        raise HTTPException(status_code=409, detail="Cannot delete the only enabled provider")
    return {"status": "ok"}


def llm_status(con: sqlite3.Connection) -> dict[str, Any]:
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT p.id, p.label, p.base_url, p.model,
               p.is_enabled, p.rate_limited_until, p.created_at,
               p.execution_fail_count,
               COUNT(l.id) AS used_today,
               SUM(CASE WHEN l.status = 'ok' THEN 1 ELSE 0 END) AS ok_calls,
               (SELECT status FROM llmbroker_call_log
                 WHERE provider_label = p.label
                 ORDER BY id DESC LIMIT 1) AS last_status,
               (SELECT error_detail FROM llmbroker_call_log
                 WHERE provider_label = p.label AND error_detail IS NOT NULL
                 ORDER BY id DESC LIMIT 1) AS last_error_detail
          FROM llmbroker_providers p
          LEFT JOIN llmbroker_call_log l ON l.provider_label = p.label
               AND date(l.called_at) = date('now')
         GROUP BY p.id
         ORDER BY p.id
        """,
    ).fetchall()
    provider_list = [
        {
            "id": int(r["id"]),
            "label": str(r["label"]),
            "base_url": str(r["base_url"]),
            "model": str(r["model"]),
            "is_enabled": bool(r["is_enabled"]),
            "rate_limited_until": r["rate_limited_until"],
            "created_at": r["created_at"],
            "execution_fail_count": int(r["execution_fail_count"] or 0),
            "used_today": int(r["used_today"] or 0),
            "ok_calls": int(r["ok_calls"] or 0),
            "last_status": r["last_status"],
            "last_error_detail": r["last_error_detail"],
        }
        for r in rows
    ]
    enabled = [p for p in provider_list if p["is_enabled"]]
    total = len(enabled)
    healthy = sum(1 for p in enabled if p["rate_limited_until"] is None)
    health = {
        "healthy": healthy,
        "total": total,
        "strategy": "failover" if total >= 2 else None,
    }
    return {
        "health": health,
        "providers": provider_list,
    }
