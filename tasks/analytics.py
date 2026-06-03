"""inv analytics — sync ledger replica, start MCP server, open Marimo dashboard."""

import shutil
import subprocess
import tomllib
from pathlib import Path

from invoke import task

from dinary.config import settings

_LLM_PROVIDERS_TOML = Path(__file__).resolve().parents[1] / ".deploy" / "llm_providers.toml"

_DATA_DIR = Path(settings.data_path).parent
_REPLICA_PATH = _DATA_DIR / "ledger-replica.db"
_NOTEBOOKS_DIR = Path("src/dinary_analytics/notebooks")
_DEFAULT_MARIMO_PORT = 2718
_DEFAULT_MCP_PORT = 8765


def _sync_replica() -> None:
    _DATA_DIR.mkdir(exist_ok=True)
    shutil.copy2(settings.data_path, _REPLICA_PATH)
    print(f"Synced ledger replica → {_REPLICA_PATH}")


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


@task(
    help={
        "port": f"Marimo dashboard port (default {_DEFAULT_MARIMO_PORT}).",
        "mcp-port": f"MCP SSE server port (default {_DEFAULT_MCP_PORT}).",
    },
)
def analytics(c, port=_DEFAULT_MARIMO_PORT, mcp_port=_DEFAULT_MCP_PORT):
    """Sync ledger replica, start MCP server, and open Marimo dashboard."""
    _sync_replica()
    extra_env: dict[str, str] = {}
    gemini_key = _gemini_api_key()
    if gemini_key:
        extra_env["GOOGLE_AI_STUDIO_API_KEY"] = gemini_key
    else:
        print("Warning: no Gemini provider found in .deploy/llm_providers.toml — AI chat disabled.")
    mcp_proc = subprocess.Popen(
        [
            "uv",
            "run",
            "python",
            "-m",
            "dinary_analytics.mcp_server",
            "--port",
            str(mcp_port),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    print(f"MCP server started on port {mcp_port} (PID {mcp_proc.pid})")
    try:
        c.run(
            f"uv run marimo run {_NOTEBOOKS_DIR / 'dashboard.py'} --port {port} --no-token",
            pty=True,
            env=extra_env,
        )
    finally:
        mcp_proc.terminate()
        mcp_proc.wait()
        print("MCP server stopped.")
