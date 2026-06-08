"""inv analytics — ensure dinary-ai is running, start Marimo dashboard."""

import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

from invoke import task

from dinary_analytics.paths import MCP_PORT

_LLM_PROVIDERS_TOML = Path(__file__).resolve().parents[1] / ".deploy" / "llm_providers.toml"

_NOTEBOOKS_DIR = Path("src/dinary_analytics/notebooks")
_DEFAULT_MARIMO_PORT = 2718


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


def _gemini_api_key() -> str | None:
    if not _LLM_PROVIDERS_TOML.exists():
        return None
    with _LLM_PROVIDERS_TOML.open("rb") as f:
        data = tomllib.load(f)
    for provider in data.get("providers", []):
        url = provider.get("base_url", "").lower()
        if "gemini" in url or "googleapis" in url or "aistudio" in url:
            return provider.get("api_key")
    return None


@task(help={"port": f"Marimo dashboard port (default {_DEFAULT_MARIMO_PORT})."})
def analytics(c, port=_DEFAULT_MARIMO_PORT):
    """Ensure dinary-ai is running, then open Marimo dashboard."""
    _ensure_dinary_ai(c)
    extra_env: dict[str, str] = {}
    gemini_key = _gemini_api_key()
    if gemini_key:
        extra_env["GOOGLE_AI_STUDIO_API_KEY"] = gemini_key
    else:
        print("Warning: no Gemini provider found in .deploy/llm_providers.toml — AI chat disabled.")
    c.run(
        f"uv run marimo run {_NOTEBOOKS_DIR / 'dashboard.py'} --port {port} --no-token",
        pty=True,
        env=extra_env,
    )
