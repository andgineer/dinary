import base64
import os
import re
import warnings
from pathlib import Path

from pydantic_settings import BaseSettings

_SPREADSHEET_URL_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]+)")


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
    "DINARY_SHEET_IMPORT_SOURCES_JSON": "DINARY_IMPORT_SOURCES_JSON",
    "DINARY_GOOGLE_SHEETS_SPREADSHEET_ID": "DINARY_SHEET_LOGGING_SPREADSHEET",
}


def _warn_deprecated_env_vars() -> None:
    """Warn when old env var names are still present.

    Settings uses ``extra="ignore"``, so stale keys would otherwise be
    silently ignored after the rename. Warn loudly to make upgrades
    self-diagnosing without restoring the old names as supported config.
    """
    for old_name, new_name in _DEPRECATED_ENV_RENAMES.items():
        if os.getenv(old_name):
            warnings.warn(
                f"{old_name} is deprecated and ignored; rename it to {new_name}.",
                UserWarning,
                stacklevel=2,
            )


class Settings(BaseSettings):
    model_config = {"env_prefix": "DINARY_", "env_file": ".env", "extra": "ignore"}

    google_sheets_credentials_path: Path = _GSPREAD_DEFAULT
    import_sources_json: str = ""
    sheet_logging_spreadsheet: str = ""

    app_currency: str = "RSD"
    data_path: str = "data/dinary.duckdb"

    sheet_logging_drain_interval_sec: float = 300.0
    sheet_logging_drain_max_attempts_per_iteration: int = 15
    sheet_logging_drain_inter_row_delay_sec: float = 1.0

    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8000
    log_level: str = "info"
    log_json: bool = False


settings = Settings()
_warn_deprecated_env_vars()
_materialize_b64_credentials(settings.google_sheets_credentials_path)
