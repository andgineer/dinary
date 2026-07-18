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


def _llm_api_keys() -> dict[str, str]:
    """Resolve every provider's ``api_key_ref`` from ``.deploy/.env``, keyed by ref.

    The analytics broker inside the marimo process resolves keys from its
    environment by the exact ref name in ``llms.toml``, so the values must be
    exported under those names unchanged. Providers whose ref is missing from
    the env file are reported and skipped — the broker treats them as keyless.
    """
    if not _LLM_TOML.exists():
        raise SystemExit(f".deploy/llms.toml not found at {_LLM_TOML} — cannot resolve LLM keys")
    configs = asyncio.run(llmbroker.Registry(_LLM_TOML).load())
    env = dotenv_values(LOCAL_ENV_PATH)
    keys: dict[str, str] = {}
    missing: list[str] = []
    seen: set[str] = set()
    for cfg in configs:
        ref = cfg.api_key_ref
        if ref in seen:
            continue
        seen.add(ref)
        value = env.get(ref)
        if value:
            keys[ref] = value
        else:
            missing.append(f"{cfg.name} ({ref})")
    if missing:
        print(f"Warning: no key in {LOCAL_ENV_PATH} for: {', '.join(missing)}")
    return keys


@task(help={"port": f"Marimo dashboard port (default {_DEFAULT_MARIMO_PORT})."})
def analytics(c, port=_DEFAULT_MARIMO_PORT):
    """Ensure dinary-ai is running, then open Marimo dashboard."""
    _ensure_dinary_ai(c)
    extra_env = _llm_api_keys()
    if not extra_env:
        print("Warning: no LLM API keys resolved — AI chat disabled.")
    c.run(
        f"uv run marimo run {_NOTEBOOKS_DIR / 'dashboard.py'} --port {port} --no-token "
        "--no-skew-protection",
        pty=True,
        env=extra_env,
    )
