"""LLM provider API: /api/llm/*"""

import llmbroker
from fastapi import APIRouter, Request

from dinary.api.controllers.llm import (
    ProviderIn,
    ProviderPatch,
    add_provider,
    delete_provider,
    list_providers,
    llm_status,
    update_provider,
)

router = APIRouter()


def _get_llms(request: Request) -> llmbroker.AsyncBroker:
    return request.app.state.llms


@router.get("/api/llm/providers")
async def get_providers(request: Request) -> list[dict]:
    return await list_providers(_get_llms(request))


@router.post("/api/llm/providers", status_code=201)
async def create_provider(body: ProviderIn, request: Request) -> dict:
    return await add_provider(body, _get_llms(request))


@router.patch("/api/llm/providers/{provider_name}")
async def patch_provider(provider_name: str, body: ProviderPatch, request: Request) -> dict:
    return await update_provider(provider_name, body, _get_llms(request))


@router.delete("/api/llm/providers/{provider_name}")
async def remove_provider(provider_name: str, request: Request) -> dict:
    return await delete_provider(provider_name, _get_llms(request))


@router.get("/api/llm/status")
async def get_llm_status(request: Request) -> dict:
    return await llm_status(_get_llms(request))
