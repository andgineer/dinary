"""Filesystem paths and small constants for analytics — no heavy deps, safe to import anywhere."""

import os
import sys
from pathlib import Path


def _app_data_dir() -> Path:
    """Return the platform-specific directory for dinary-ai's local app data."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "dinary"
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            raise RuntimeError("LOCALAPPDATA not set; cannot determine DB path on Windows")
        return Path(local_app_data) / "dinary"
    return Path.home() / ".local" / "share" / "dinary"


QUERIES_DIR = Path(__file__).parent / "queries"
REPLICA_PATH = _app_data_dir() / "dinary-ai.db"
ANALYTICS_DB_PATH = _app_data_dir() / "analytics.db"
LOCAL_CONFIG_PATH = _app_data_dir() / "dinary-ai-config.json"

MCP_PORT = 8765
