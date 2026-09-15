import time
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import dotenv_values
from invoke import task

from dinary_analytics.llm import key_ref
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
    """Resolve the chat model's key from ``.deploy/.env``, keyed by its ref.

    The analytics broker inside the marimo process resolves the key from its own
    environment by that exact ref name, so the value must be exported under it
    unchanged. The value comes from the env *file*, not the process environment.
    """
    ref = key_ref()
    value = dotenv_values(LOCAL_ENV_PATH).get(ref)
    if not value:
        print(f"Warning: no key in {LOCAL_ENV_PATH} for: {ref}")
        return {}
    return {ref: value}


@task(help={"port": f"Marimo dashboard port (default {_DEFAULT_MARIMO_PORT})."})
def analytics(c, port=_DEFAULT_MARIMO_PORT):
    """Ensure dinary-ai is running, then open Marimo dashboard."""
    _ensure_dinary_ai(c)
    extra_env = _llm_api_keys()
    if not extra_env:
        print("Warning: no LLM API key resolved — AI chat disabled.")
    c.run(
        f"uv run marimo run {_NOTEBOOKS_DIR / 'dashboard.py'} --port {port} --no-token "
        "--no-skew-protection",
        pty=True,
        env=extra_env,
    )
