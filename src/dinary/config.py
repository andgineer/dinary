import base64
import logging
import os
import re
import sys
import warnings
from pathlib import Path

from pydantic import computed_field
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

_SPREADSHEET_URL_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]+)")

# Anchored to the repo root so cron, systemd, and interactive `uv run` agree
# regardless of CWD.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEPLOY_DIR = _REPO_ROOT / ".deploy"
_ENV_FILE = _DEPLOY_DIR / ".env"


def _materialize_b64_credentials(target: Path) -> None:
    """Decode DINARY_GOOGLE_CREDENTIALS_BASE64 env var into a JSON file on disk.

    Useful on platforms without secret-file support (e.g. Railway).
    """
    b64 = os.getenv("DINARY_GOOGLE_CREDENTIALS_BASE64")
    if b64 and not target.exists():
        target.write_bytes(base64.b64decode(b64))


def spreadsheet_id_from_setting(raw: str) -> str | None:
    """Extract a spreadsheet ID from a bare ID or browser URL."""
    raw = raw.strip()
    if not raw:
        return None
    match = _SPREADSHEET_URL_RE.search(raw)
    if match:
        return match.group(1)
    return raw


_GSPREAD_DEFAULT = Path.home() / ".config" / "gspread" / "service_account.json"

_DEPRECATED_ENV_RENAMES = {
    "DINARY_GOOGLE_SHEETS_SPREADSHEET_ID": "DINARY_SHEET_LOGGING_SPREADSHEET",
}

#: Env vars removed outright (vs. renamed) — kept separate so the warning
#: doesn't suggest a nonexistent successor.
_DEPRECATED_ENV_REMOVED: dict[str, str] = {
    "DINARY_ADMIN_API_TOKEN": (
        "the shared-token admin gate was removed; authentication will be "
        "re-added as a proper auth layer in a later phase. Deployments must "
        "put the server behind a private network until then."
    ),
    "DINARY_IMPORT_SOURCES_JSON": (
        "the bulk-import pipeline has been removed; this env var is no longer used."
    ),
    "DINARY_SHEET_IMPORT_SOURCES_JSON": (
        "the bulk-import pipeline has been removed; this env var is no longer used."
    ),
}


def _warn_deprecated_env_vars() -> None:
    """Settings uses ``extra="ignore"``, so stale keys would otherwise be
    silently ignored after a rename — warn loudly instead."""
    for old_name, new_name in _DEPRECATED_ENV_RENAMES.items():
        if os.getenv(old_name):
            warnings.warn(
                f"{old_name} is deprecated and ignored; rename it to {new_name}.",
                UserWarning,
                stacklevel=2,
            )
    for old_name, reason in _DEPRECATED_ENV_REMOVED.items():
        if os.getenv(old_name):
            warnings.warn(
                f"{old_name} is no longer supported and is ignored: {reason}",
                UserWarning,
                stacklevel=2,
            )


def _warn_missing_env_file() -> None:
    """Gated on ``sys.modules`` (not ``PYTEST_CURRENT_TEST``, which isn't set
    until a test body runs) so collection-time import doesn't spam the warning."""
    if "pytest" in sys.modules:
        return
    if _ENV_FILE.exists():
        return
    warnings.warn(
        f"{_ENV_FILE} is missing — runtime configuration has moved "
        "to .deploy/.env. Copy .deploy.example/.env to .deploy/.env "
        "and fill in your values.",
        UserWarning,
        stacklevel=2,
    )


class Settings(BaseSettings):
    model_config = {
        "env_prefix": "DINARY_",
        "env_file": str(_ENV_FILE),
        "extra": "ignore",
    }

    google_sheets_credentials_path: Path = _GSPREAD_DEFAULT
    sheet_logging_spreadsheet: str = ""

    # UI/API default currency, see specs/reference/currencies.md.
    app_currency: str = "RSD"

    # DB-wide amount denomination, see specs/reference/currencies.md.
    accounting_currency: str = "EUR"
    data_path: str = "data/dinary.db"

    sheet_logging_drain_interval_sec: float = 300.0
    sheet_logging_drain_max_attempts_per_iteration: int = 15
    sheet_logging_drain_inter_row_delay_sec: float = 1.0

    # The drain loop polls this tab's modifiedTime and only reparses on change.
    sheet_mapping_tab_name: str = "map"

    # Bounds the startup warm-up so a slow Google backend can't starve the
    # health probe; 0 skips the warm-up entirely.
    warm_sheet_mapping_timeout_sec: float = 10.0

    receipt_classification_enabled: bool = True

    # Expense datetimes are stored in this zone; cross-DST ORDER BY comparisons
    # may be off by 1 hour, accepted as a rare edge case.
    user_timezone: str = "Europe/Belgrade"

    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8000
    log_level: str = "info"
    log_json: bool = False

    @computed_field  # type: ignore[misc]
    @property
    def sheet_logging_enabled(self) -> bool:
        """True when sheet logging is configured (DINARY_SHEET_LOGGING_SPREADSHEET is set)."""
        return bool(self.sheet_logging_spreadsheet)


settings = Settings()
_warn_deprecated_env_vars()
_warn_missing_env_file()
_materialize_b64_credentials(settings.google_sheets_credentials_path)
