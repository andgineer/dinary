import base64
import json
import logging
import os
import re
import sys
import threading
import warnings
from dataclasses import dataclass
from pathlib import Path

from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

_SPREADSHEET_URL_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]+)")

# ---------------------------------------------------------------------------
# Repo-rooted paths. All instance config (``.deploy/.env``,
# ``.deploy/import_sources.json``) is anchored to the repo root so cron,
# systemd, and interactive ``uv run`` all agree regardless of CWD.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEPLOY_DIR = _REPO_ROOT / ".deploy"
_ENV_FILE = _DEPLOY_DIR / ".env"
_IMPORT_SOURCES_PATH = _DEPLOY_DIR / "import_sources.json"


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

#: Env vars that used to be supported and are now gone with no successor.
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
        "move the list to .deploy/import_sources.json (optional — see imports/ at the repo root)."
    ),
    "DINARY_SHEET_IMPORT_SOURCES_JSON": (
        "move the list to .deploy/import_sources.json (optional — see imports/ at the repo root)."
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


# ---------------------------------------------------------------------------
# Layout helpers (moved here from seed_config to break the
# seed_config -> config dependency cycle that would otherwise form once
# ``read_import_sources`` needs ``_default_layout_for_year``).
# ---------------------------------------------------------------------------

_RUB_2012_LAST_YEAR = 2012
_RUB_2014_LAST_YEAR = 2014
_RUB_2016_LAST_YEAR = 2016
_RUB_6COL_LAST_YEAR = 2021
_RUB_FALLBACK_YEAR = 2022

#: Layout keys recognized by the historical-import / runtime-export code.
KNOWN_LAYOUT_KEYS: frozenset[str] = frozenset(
    {"default", "rub", "rub_fallback", "rub_6col", "rub_2016", "rub_2014", "rub_2012"},
)


def _default_layout_for_year(year: int) -> str:
    if year <= _RUB_2012_LAST_YEAR:
        return "rub_2012"
    if year <= _RUB_2014_LAST_YEAR:
        return "rub_2014"
    if year <= _RUB_2016_LAST_YEAR:
        return "rub_2016"
    if year <= _RUB_6COL_LAST_YEAR:
        return "rub_6col"
    if year == _RUB_FALLBACK_YEAR:
        return "rub_fallback"
    return "default"


# ---------------------------------------------------------------------------
# Import sources loader — the file-backed replacement for the
# ``import_sources`` DuckDB table.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ImportSourceRow:
    """One row of ``.deploy/import_sources.json``.

    Lives in ``dinary.config`` — alongside the ``read_import_sources``
    / ``get_import_source`` loader that produces it — because the
    import-sources registry is a file-backed configuration artifact,
    not DB state. Consumers (import and verify tasks) import the type
    and the loader together from this module.
    """

    year: int
    spreadsheet_id: str
    worksheet_name: str = ""
    layout_key: str = ""
    notes: str | None = None
    income_worksheet_name: str = ""
    income_layout_key: str = ""


_import_sources_cache_lock = threading.Lock()
_import_sources_cache: tuple[float, tuple[ImportSourceRow, ...]] | None = None


#: Shared hint appended to user-facing error messages that talk about
#: ``.deploy/import_sources.json``. ``imports.seed`` re-imports this
#: constant so every error message points at the same place without
#: drifting over time.
IMPORT_SOURCES_DOC_HINT = "See the ``imports/`` directory at the repo root for schema details."


def _parse_import_sources_rows(payload: object) -> tuple[ImportSourceRow, ...]:
    # TRY004 suppressions below: a malformed ``import_sources.json`` is an
    # operator-facing config error, not a caller-side type bug, so we
    # deliberately surface ``RuntimeError`` (which the call sites and the
    # test-suite key off) rather than ``TypeError``.
    if not isinstance(payload, list):
        msg = (
            f"{_IMPORT_SOURCES_PATH} must be a JSON array of row objects. {IMPORT_SOURCES_DOC_HINT}"
        )
        raise RuntimeError(msg)  # noqa: TRY004
    rows: list[ImportSourceRow] = []
    for raw in payload:
        if not isinstance(raw, dict):
            msg = (
                f"{_IMPORT_SOURCES_PATH} contains a non-object entry: {raw!r}. "
                f"{IMPORT_SOURCES_DOC_HINT}"
            )
            raise RuntimeError(msg)  # noqa: TRY004
        if "year" not in raw:
            msg = (
                f"{_IMPORT_SOURCES_PATH} entry missing required 'year' field: {raw!r}. "
                f"{IMPORT_SOURCES_DOC_HINT}"
            )
            raise RuntimeError(msg)
        try:
            year = int(raw["year"])
        except (TypeError, ValueError) as exc:
            msg = (
                f"{_IMPORT_SOURCES_PATH} entry has non-integer 'year' field: {raw!r}. "
                f"{IMPORT_SOURCES_DOC_HINT}"
            )
            raise RuntimeError(msg) from exc
        notes = raw.get("notes")
        if notes is not None and not isinstance(notes, str):
            msg = (
                f"{_IMPORT_SOURCES_PATH} entry has non-string 'notes' field: {raw!r}. "
                f"{IMPORT_SOURCES_DOC_HINT}"
            )
            raise RuntimeError(msg)
        rows.append(
            ImportSourceRow(
                year=year,
                spreadsheet_id=str(raw.get("spreadsheet_id", "")),
                worksheet_name=str(raw.get("worksheet_name", "")),
                layout_key=str(raw.get("layout_key") or _default_layout_for_year(year)),
                notes=notes,
                income_worksheet_name=str(raw.get("income_worksheet_name", "")),
                income_layout_key=str(raw.get("income_layout_key", "")),
            ),
        )
    return tuple(rows)


def read_import_sources() -> list[ImportSourceRow]:
    """Load import-sources from ``.deploy/import_sources.json``.

    The file is OPTIONAL — it only matters for callers that run
    ``inv import-*`` tasks (bootstrap, per-year import, verify, report).
    The runtime path (FastAPI + sheet logging + map reload) does not
    touch this loader, so users who don't care about imports can
    deploy dinary without ever creating the file.

    Contract:

    * Missing file -> returns ``[]``. NOT an exception. Callers that
      REQUIRE a non-empty list raise themselves with an actionable
      message pointing at the repo-root ``imports/`` directory.
    * Malformed JSON / wrong shape -> ``RuntimeError`` with a pointer
      to ``imports/``. The distinction is intentional: absent is a
      valid user choice; corrupt is a bug.
    * Path anchored to repo root (via ``__file__``), NOT CWD — so
      cron / systemd / interactive ``uv run`` all agree.
    * mtime-keyed in-process cache guarded by ``threading.Lock`` for
      multi-worker uvicorn safety. Subsequent edits picked up on next
      call without a service restart.
    * No placeholder-value pre-check. If the user copied
      ``.deploy.example/import_sources.json`` as-is, and then calls
      ``inv import-*``, the Google Sheets API will return a natural
      permission / 404 error — self-explanatory and already actionable.
      Non-import users never hit this code path.
    """
    global _import_sources_cache  # noqa: PLW0603

    try:
        mtime = _IMPORT_SOURCES_PATH.stat().st_mtime
    except FileNotFoundError:
        with _import_sources_cache_lock:
            _import_sources_cache = None
        return []

    cached = _import_sources_cache
    if cached is not None and cached[0] == mtime:
        return list(cached[1])

    with _import_sources_cache_lock:
        cached = _import_sources_cache
        if cached is not None and cached[0] == mtime:
            return list(cached[1])

        try:
            raw = _IMPORT_SOURCES_PATH.read_text(encoding="utf-8")
        except FileNotFoundError:
            _import_sources_cache = None
            return []
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            msg = f"{_IMPORT_SOURCES_PATH} is not valid JSON: {exc}. {IMPORT_SOURCES_DOC_HINT}"
            raise RuntimeError(msg) from exc

        rows = _parse_import_sources_rows(payload)
        _import_sources_cache = (mtime, rows)
        return list(rows)


def get_import_source(year: int) -> ImportSourceRow | None:
    """Return the ``ImportSourceRow`` for ``year`` or ``None``.

    Thin wrapper over ``read_import_sources()`` so callers that only
    care about a single year don't have to iterate the full list.
    """
    for row in read_import_sources():
        if row.year == year:
            return row
    return None


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
    data_path: str = "data/dinary.duckdb"

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

    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8000
    log_level: str = "info"
    log_json: bool = False


settings = Settings()
_warn_deprecated_env_vars()
_warn_missing_env_file()
_materialize_b64_credentials(settings.google_sheets_credentials_path)
