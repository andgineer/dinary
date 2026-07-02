import asyncio
import time
import urllib.error
import urllib.request
from pathlib import Path

import llmbroker
from dotenv import dotenv_values
from invoke import task

from dinary_analytics.paths import MCP_PORT
from tasks.devtools.constants import LOCAL_ENV_PATH

_LLM_TOML = Path(__file__).resolve().parents[1] / ".deploy" / "llms.toml"

_NOTEBOOKS_DIR = Path("src/dinary_analytics/notebooks")
_DEFAULT_MARIMO_PORT = 2718

_GEMINI_URLS = ("generativelanguage.googleapis.com", "aistudio.google.com")


def _ensure_dinary_ai(c) -> None:
    try:
        urllib.request.urlopen(f"http://localhost:{MCP_PORT}/health", timeout=2)
        print(f"OK: dinary-ai reachable on port {MCP_PORT}")
        return
    except (urllib.error.URLError, OSError):
        pass
    print(f"dinary-ai not running on port {MCP_PORT} — running setup-dinary-ai")
    c.run("uv run inv setup-dinary-ai")
    for _ in range(30):
        time.sleep(1)
        try:
            urllib.request.urlopen(f"http://localhost:{MCP_PORT}/health", timeout=2)
            print(f"OK: dinary-ai reachable on port {MCP_PORT}")
            return
        except (urllib.error.URLError, OSError):
            pass
    raise SystemExit(f"dinary-ai did not start on port {MCP_PORT} after setup")


async def _find_gemini_key_ref() -> str | None:
    configs = await llmbroker.Registry(_LLM_TOML).load()
    for cfg in configs:
        if any(u in cfg.base_url for u in _GEMINI_URLS):
            return cfg.api_key_ref
    return None


def _gemini_api_key() -> str | None:
    if not _LLM_TOML.exists():
        raise SystemExit(f".deploy/llms.toml not found at {_LLM_TOML} — cannot resolve LLM keys")
    ref = asyncio.run(_find_gemini_key_ref())
    if ref is None:
        return None
    env = dotenv_values(LOCAL_ENV_PATH)
    key = env.get(ref)
    if key is None:
        raise SystemExit(f"{ref!r} not found in {LOCAL_ENV_PATH} — add it and retry")
    return key


@task(help={"port": f"Marimo dashboard port (default {_DEFAULT_MARIMO_PORT})."})
def analytics(c, port=_DEFAULT_MARIMO_PORT):
    """Ensure dinary-ai is running, then open Marimo dashboard."""
    _ensure_dinary_ai(c)
    extra_env: dict[str, str] = {}
    gemini_key = _gemini_api_key()
    if gemini_key:
        extra_env["GOOGLE_AI_STUDIO_API_KEY"] = gemini_key
    else:
        print("Warning: no Gemini provider in .deploy/llms.toml — AI chat disabled.")
    c.run(
        f"uv run marimo run {_NOTEBOOKS_DIR / 'dashboard.py'} --port {port} --no-token "
        "--no-skew-protection",
        pty=True,
        env=extra_env,
    )
