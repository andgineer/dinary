"""LLM provider business logic — broker-only, no raw SQL."""

import llmbroker
from fastapi import HTTPException
from llmbroker.models import LLMConfig, LLMSnapshot
from pydantic import BaseModel


class ProviderIn(BaseModel):
    name: str
    base_url: str
    api_key_ref: str
    model: str


class ProviderPatch(BaseModel):
    base_url: str | None = None
    api_key_ref: str | None = None
    model: str | None = None


def _snapshot_to_dict(name: str, snap: LLMSnapshot) -> dict:
    return {
        "name": name,
        "label": name,
        "base_url": snap.config.base_url,
        "model": snap.config.model,
        "api_key_ref": snap.config.api_key_ref,
        "rate_limited_until": (
            snap.state.cooldown_until.isoformat() if snap.state.cooldown_until else None
        ),
        "execution_fail_count": snap.state.fail_count,
        "used_today": snap.metrics.call_count if snap.metrics else 0,
        "last_status": (
            snap.metrics.last_status.value if snap.metrics and snap.metrics.last_status else None
        ),
    }


async def list_providers(llms: llmbroker.AsyncBroker) -> list[dict]:
    snapshot = await llms.snapshot()
    return [_snapshot_to_dict(name, snap) for name, snap in snapshot.items()]


async def add_provider(body: ProviderIn, llms: llmbroker.AsyncBroker) -> dict:
    cfg = LLMConfig(
        name=body.name,
        base_url=body.base_url,
        model=body.model,
        api_key_ref=body.api_key_ref,
    )
    await llms.add(cfg)
    return {"name": body.name}


async def update_provider(name: str, body: ProviderPatch, llms: llmbroker.AsyncBroker) -> dict:
    snapshot = await llms.snapshot()
    if name not in snapshot:
        raise HTTPException(status_code=404, detail="Provider not found")
    old = snapshot[name].config
    updated = LLMConfig(
        name=name,
        base_url=body.base_url if body.base_url is not None else old.base_url,
        model=body.model if body.model is not None else old.model,
        api_key_ref=body.api_key_ref if body.api_key_ref is not None else old.api_key_ref,
    )
    await llms.add(updated)
    return {"status": "ok"}


async def delete_provider(name: str, llms: llmbroker.AsyncBroker) -> dict:
    snapshot = await llms.snapshot()
    if name not in snapshot:
        raise HTTPException(status_code=404, detail="Provider not found")
    enabled = [n for n in snapshot if snapshot[n].state.phase.value != "offline"]
    if len(enabled) <= 1 and name in enabled:
        raise HTTPException(status_code=409, detail="Cannot delete the only enabled provider")
    await llms.remove(name)
    return {"status": "ok"}


async def llm_status(llms: llmbroker.AsyncBroker) -> dict:
    snapshot = await llms.snapshot()
    provider_list = [_snapshot_to_dict(name, snap) for name, snap in snapshot.items()]
    total = len(provider_list)
    healthy = sum(1 for p in provider_list if p["rate_limited_until"] is None)
    health = {
        "healthy": healthy,
        "total": total,
        "strategy": "failover" if total >= 2 else None,
    }
    return {"health": health, "providers": provider_list}
