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

# ---------------------------------------------------------------------------
# Repo-rooted paths. All instance config (``.deploy/.env``) is anchored
# to the repo root so cron, systemd, and interactive ``uv run`` all agree
# regardless of CWD.
# ---------------------------------------------------------------------------

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

#: Env vars that are no longer recognised and have no successor.
#: Unlike ``_DEPRECATED_ENV_RENAMES`` the warning does not suggest a new
#: name — the feature was removed outright. Kept separate so the loop
#: below can format its message appropriately.
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
    for old_name, reason in _DEPRECATED_ENV_REMOVED.items():
        if os.getenv(old_name):
            warnings.warn(
                f"{old_name} is no longer supported and is ignored: {reason}",
                UserWarning,
                stacklevel=2,
            )


def _warn_missing_env_file() -> None:
    """Warn once at startup if ``.deploy/.env`` is missing.

    Gated on ``"pytest" not in sys.modules`` so test runs (which import
    ``dinary.config`` during collection) don't spam the warning. Using
    ``sys.modules`` — not ``PYTEST_CURRENT_TEST`` — because the latter
    is only set around individual test bodies, but config.py is imported
    during collection where the env var hasn't been set yet.
    """
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

    # PWA / API user-facing default currency: the currency the UI
    # works in and the fallback for ``POST /api/expenses`` requests
    # that omit ``currency``. Typical ``expenses.currency_original``
    # matches this value, since users type amounts in ``app_currency``.
    app_currency: str = "RSD"

    # Canonical accounting currency: ``expenses.amount`` and
    # ``income.amount`` always live in this currency, and every
    # ``inv report-*`` total is rendered in it. Source amounts from
    # sheets / QR / PWA are recorded verbatim in
    # ``expenses.amount_original`` + ``currency_original``; the
    # NBS-anchored conversion to ``accounting_currency`` is what
    # lives in ``amount``. The PWA default (``app_currency``) stays
    # RSD because the user types in dinars; this setting is what the
    # DB is denominated in.
    accounting_currency: str = "EUR"
    data_path: str = "data/dinary.db"

    sheet_logging_drain_interval_sec: float = 300.0
    sheet_logging_drain_max_attempts_per_iteration: int = 15
    sheet_logging_drain_inter_row_delay_sec: float = 1.0

    # Name of the worksheet tab on ``sheet_logging_spreadsheet`` that
    # holds the curated 3D->2D runtime routing table. The drain loop
    # polls this tab's ``modifiedTime`` via Drive API and only reparses
    # the contents when the timestamp changes.
    sheet_mapping_tab_name: str = "map"

    # Startup preload budget for ``sheet_mapping.reload_now``. Bounded
    # so a slow or unreachable Google backend cannot wedge lifespan
    # startup and starve Railway's health probe. On timeout the drain
    # loop retries on its own schedule, and the first expense pays
    # the uncached Drive+Sheets round-trip (~1s). Raise for slow
    # hosts; set to 0 to skip the warm-up entirely.
    warm_sheet_mapping_timeout_sec: float = 10.0

    receipt_classification_enabled: bool = True

    # IANA timezone name used when storing expense timestamps.
    # All expense datetimes are converted to this zone before writing to DB
    # so that ORDER BY datetime DESC is consistent within the same UTC offset.
    # Cross-DST comparisons (e.g. a +01:00 winter receipt vs a +02:00 summer
    # receipt) may be off by 1 hour — accepted as an extremely rare edge case.
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
