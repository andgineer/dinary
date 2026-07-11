"""LLM provider API: /api/llm/* — read-only status plus user disable/enable."""

import llmbroker
from fastapi import APIRouter, Request, Response

from dinary.api.controllers.llm import llm_status, set_provider_disabled
from dinary.config import settings

router = APIRouter()


def _get_llms(request: Request) -> llmbroker.AsyncBroker:
    return request.app.state.llms


def _get_optimizer(request: Request) -> llmbroker.Optimizer:
    return request.app.state.llm_optimizer


@router.get("/api/llm/status")
async def get_llm_status(request: Request) -> dict:
    return await llm_status(
        _get_llms(request),
        _get_optimizer(request),
        settings.llm_providers_file,
    )


@router.post("/api/llm/providers/{provider_name}/disable", status_code=204)
async def disable_provider(provider_name: str, request: Request) -> Response:
    await set_provider_disabled(provider_name, disabled=True, llms=_get_llms(request))
    return Response(status_code=204)


@router.post("/api/llm/providers/{provider_name}/enable", status_code=204)
async def enable_provider(provider_name: str, request: Request) -> Response:
    await set_provider_disabled(provider_name, disabled=False, llms=_get_llms(request))
    return Response(status_code=204)
