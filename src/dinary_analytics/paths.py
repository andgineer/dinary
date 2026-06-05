"""Filesystem paths for analytics data — no heavy deps, safe to import anywhere."""

from pathlib import Path

from dinary.config import settings

_DATA_DIR = Path(settings.data_path).parent

QUERIES_DIR = Path(__file__).parent / "queries"
REPLICA_PATH = _DATA_DIR / "ledger-replica.db"
ANALYTICS_DB_PATH = _DATA_DIR / "analytics.db"
