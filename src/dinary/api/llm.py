"""LLM provider API: /api/llm/*"""

import sqlite3

from fastapi import APIRouter, Depends

from dinary.api.controllers.llm import (
    ProviderIn,
    ProviderPatch,
    add_provider,
    delete_provider,
    list_providers,
    llm_status,
    update_provider,
)
from dinary.db.storage import get_db

router = APIRouter()


@router.get("/api/llm/providers")
def get_providers(con: sqlite3.Connection = Depends(get_db)) -> list[dict]:  # noqa: B008
    return list_providers(con)


@router.post("/api/llm/providers", status_code=201)
def create_provider(body: ProviderIn, con: sqlite3.Connection = Depends(get_db)) -> dict:  # noqa: B008
    return add_provider(body, con)


@router.patch("/api/llm/providers/{provider_id}")
def patch_provider(
    provider_id: int,
    body: ProviderPatch,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> dict:
    return update_provider(provider_id, body, con)


@router.delete("/api/llm/providers/{provider_id}")
def remove_provider(
    provider_id: int,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> dict:
    return delete_provider(provider_id, con)


@router.get("/api/llm/status")
def get_llm_status(con: sqlite3.Connection = Depends(get_db)) -> dict:  # noqa: B008
    return llm_status(con)
