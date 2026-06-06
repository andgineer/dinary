"""inv analytics — sync ledger replica, start Marimo dashboard."""

import tomllib
import urllib.error
import urllib.request
from pathlib import Path

from invoke import task

from dinary_analytics.paths import REPLICA_PATH
from tasks.backups.backups_replica import _build_replica_restore_script
from tasks.ssh_utils import ssh_replica_capture_bytes

_LLM_PROVIDERS_TOML = Path(__file__).resolve().parents[1] / ".deploy" / "llm_providers.toml"

_NOTEBOOKS_DIR = Path("src/dinary_analytics/notebooks")
_DEFAULT_MARIMO_PORT = 2718
_DEFAULT_MCP_PORT = 8765


def _sync_replica() -> None:
    REPLICA_PATH.parent.mkdir(parents=True, exist_ok=True)
    db_bytes = ssh_replica_capture_bytes(_build_replica_restore_script(None))
    REPLICA_PATH.write_bytes(db_bytes)
    print(f"Synced ledger replica from VM2 → {REPLICA_PATH} ({len(db_bytes) / 1024:.0f} KB)")


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


def _dinary_ai_running(mcp_port: int) -> bool:
    try:
        urllib.request.urlopen(f"http://localhost:{mcp_port}/mcp", timeout=2)
        return True
    except (urllib.error.URLError, OSError):
        return False


@task(
    help={
        "port": f"Marimo dashboard port (default {_DEFAULT_MARIMO_PORT}).",
        "mcp-port": f"dinary-ai MCP port (default {_DEFAULT_MCP_PORT}).",
    },
)
def analytics(c, port=_DEFAULT_MARIMO_PORT, mcp_port=_DEFAULT_MCP_PORT):
    """Ensure dinary-ai is running, then open Marimo dashboard."""
    if _dinary_ai_running(mcp_port):
        print(f"OK: dinary-ai reachable on port {mcp_port}")
    else:
        print(f"dinary-ai not running on port {mcp_port} — running setup-dinary-ai")
        c.run("uv run inv setup-dinary-ai", pty=True)
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
