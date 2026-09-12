import time
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import dotenv_values
from invoke import task

from dinary_analytics.llm import key_refs
from dinary_analytics.paths import MCP_PORT
from tasks.devtools.constants import LOCAL_ENV_PATH

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
    """Resolve every pool provider's ``api_key_ref`` from ``.deploy/.env``, keyed by ref.

    The analytics broker inside the marimo process resolves keys from its own
    environment by those exact ref names, so the values must be exported under them
    unchanged. The values come from the env *file*, not the process environment.
    Refs missing from the file are reported and skipped — the broker treats those
    providers as keyless and routes over the rest.
    """
    env = dotenv_values(LOCAL_ENV_PATH)
    keys: dict[str, str] = {}
    missing: list[str] = []
    for ref in key_refs():
        value = env.get(ref)
        if value:
            keys[ref] = value
        else:
            missing.append(ref)
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
