"""LLM provider admin API.

GET  /api/admin/llm-providers       — list all providers
POST /api/admin/llm-providers       — add provider
PATCH /api/admin/llm-providers/{id} — update label/model/api_key/priority/is_enabled
DELETE /api/admin/llm-providers/{id} — remove (refuses if only enabled provider)
POST /api/admin/llm-providers/{id}/test — fire a minimal classification call
GET  /api/admin/llm-status          — all providers with usage stats and rate_limited_until
"""

import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from dinary.services import ledger_repo
from dinary.services.llm_client import OpenAICompatibleClient

router = APIRouter()


class ProviderIn(BaseModel):
    label: str
    base_url: str
    api_key: str
    model: str
    priority: int = 0
    is_enabled: bool = True


class ProviderPatch(BaseModel):
    label: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    priority: int | None = None
    is_enabled: bool | None = None


def _row_to_dict(row: tuple) -> dict[str, Any]:
    return {
        "id": row[0],
        "label": row[1],
        "base_url": row[2],
        "model": row[3],
        "priority": row[4],
        "is_enabled": bool(row[5]),
        "rate_limited_until": row[6],
        "created_at": row[7],
    }


@router.get("/api/admin/llm-providers")
def list_providers() -> list[dict]:
    conn = ledger_repo.get_connection()
    try:
        rows = conn.execute(
            "SELECT id, label, base_url, model, priority, is_enabled,"
            "       rate_limited_until, created_at"
            " FROM llm_providers ORDER BY priority, id",
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/api/admin/llm-providers", status_code=201)
def add_provider(body: ProviderIn) -> dict:
    conn = ledger_repo.get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO llm_providers (label, base_url, api_key, model, priority, is_enabled)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [
                body.label,
                body.base_url,
                body.api_key,
                body.model,
                body.priority,
                1 if body.is_enabled else 0,
            ],
        )
        provider_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("COMMIT")
        return {"id": int(provider_id)}
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


@router.patch("/api/admin/llm-providers/{provider_id}")
def update_provider(provider_id: int, body: ProviderPatch) -> dict:
    conn = ledger_repo.get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM llm_providers WHERE id = ?",
            [provider_id],
        ).fetchone()
        if row is None:
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
        if body.priority is not None:
            updates.append("priority = ?")
            params.append(body.priority)
        if body.is_enabled is not None:
            updates.append("is_enabled = ?")
            params.append(1 if body.is_enabled else 0)

        if updates:
            conn.execute("BEGIN IMMEDIATE")
            try:
                params.append(provider_id)
                conn.execute(
                    f"UPDATE llm_providers SET {', '.join(updates)} WHERE id = ?",  # noqa: S608
                    params,
                )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

        return {"status": "ok"}
    finally:
        conn.close()


@router.delete("/api/admin/llm-providers/{provider_id}")
def delete_provider(provider_id: int) -> dict:
    conn = ledger_repo.get_connection()
    try:
        if (
            conn.execute("SELECT id FROM llm_providers WHERE id = ?", [provider_id]).fetchone()
            is None
        ):
            raise HTTPException(status_code=404, detail="Provider not found")

        # Re-read enabled_count inside the transaction so the guard and the DELETE
        # are atomic — prevents two concurrent requests from both bypassing the
        # "only enabled provider" check and deleting the entire pool.
        conflict = False
        conn.execute("BEGIN IMMEDIATE")
        try:
            enabled_count = conn.execute(
                "SELECT COUNT(*) FROM llm_providers WHERE is_enabled = 1",
            ).fetchone()[0]
            is_enabled = conn.execute(
                "SELECT is_enabled FROM llm_providers WHERE id = ?",
                [provider_id],
            ).fetchone()[0]
            if enabled_count <= 1 and is_enabled:
                conflict = True
            else:
                conn.execute("DELETE FROM llm_providers WHERE id = ?", [provider_id])
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

        if conflict:
            raise HTTPException(status_code=409, detail="Cannot delete the only enabled provider")
        return {"status": "ok"}
    finally:
        conn.close()


@router.post("/api/admin/llm-providers/{provider_id}/test")
async def test_provider(provider_id: int) -> dict:
    conn = ledger_repo.get_connection()
    try:
        row = conn.execute(
            "SELECT base_url, api_key, model FROM llm_providers WHERE id = ?",
            [provider_id],
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Provider not found")
        base_url, api_key, model = str(row[0]), str(row[1]), str(row[2])
    finally:
        conn.close()

    try:
        client = OpenAICompatibleClient(base_url, api_key, model)
        results = await client.classify_receipt(
            ["хлеб"],
            "Test Store",
            {1: "Food", 2: "Non-food"},
        )
        return {"status": "ok", "items_classified": len(results)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": str(exc)}


@router.get("/api/admin/llm-status")
def llm_status() -> dict:
    conn = ledger_repo.get_connection()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT p.id, p.label, p.base_url, p.model, p.priority,
                   p.is_enabled, p.rate_limited_until, p.created_at,
                   COUNT(l.id) AS used_today,
                   SUM(CASE WHEN l.status = 'ok' THEN 1 ELSE 0 END) AS ok_calls,
                   (SELECT status FROM llm_call_log
                     WHERE provider_id = p.id
                     ORDER BY id DESC LIMIT 1) AS last_status
              FROM llm_providers p
              LEFT JOIN llm_call_log l ON l.provider_id = p.id
             GROUP BY p.id
             ORDER BY p.priority, p.id
            """,
        ).fetchall()
        provider_list = [
            {
                "id": int(r["id"]),
                "label": str(r["label"]),
                "base_url": str(r["base_url"]),
                "model": str(r["model"]),
                "priority": int(r["priority"]),
                "is_enabled": bool(r["is_enabled"]),
                "rate_limited_until": r["rate_limited_until"],
                "created_at": r["created_at"],
                "used_today": int(r["used_today"] or 0),
                "ok_calls": int(r["ok_calls"] or 0),
                "last_status": r["last_status"],
            }
            for r in rows
        ]

        meta = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT key, value FROM app_metadata"
                " WHERE key IN ('llm_last_provider_idx','llm_provider_switch_last',"
                "               'llm_provider_switch_count','llm_all_exhausted_last')",
            ).fetchall()
        }

        enabled = [p for p in provider_list if p["is_enabled"]]
        total = len(enabled)
        healthy = sum(1 for p in enabled if p["rate_limited_until"] is None)
        health = {
            "healthy": healthy,
            "total": total,
            "strategy": "failover" if total >= 2 else None,
            "last_switch": meta.get("llm_provider_switch_last"),
        }

        return {"health": health, "providers": provider_list, "meta": meta}
    finally:
        conn.close()
