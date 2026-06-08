"""Background daemon that refreshes the local ledger replica from the dinary server."""

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from dinary_analytics.paths import LOCAL_CONFIG_PATH, REPLICA_PATH

logger = logging.getLogger(__name__)

REFRESH_INTERVAL_SECONDS = 86400  # 24 hours — normal poll interval after success; manual
# "Refresh now" (Step 2/5) wakes the loop early, so a once-a-day floor is enough to bound
# staleness for users who never click it
REFRESH_RETRY_BASE_SECONDS = 30  # first retry after error; doubles each failure, capped
# at REFRESH_INTERVAL_SECONDS

_SQLITE_MAGIC_HEADER = b"SQLite format 3\x00"

_lock: threading.Lock = threading.Lock()
_db_path: Path | None = None  # None = no successful refresh yet
_last_refresh: float | None = None  # time.time() timestamp
_last_refresh_error: str | None = None
_daemon_thread: threading.Thread | None = None
_wake_event: threading.Event = threading.Event()  # set by trigger_refresh_now() to cut
# the current wait short


class RefreshError(Exception):
    """Raised when the local ledger replica cannot be refreshed from the server."""


def get_app_url() -> str | None:
    """Return the configured dinary server address, or None if it isn't set yet."""
    try:
        return json.loads(LOCAL_CONFIG_PATH.read_text()).get("app_url") or None
    except (OSError, json.JSONDecodeError):
        return None


def set_app_url(url: str) -> None:
    """Persist the dinary server address to the local config file."""
    _url = url.strip().rstrip("/")
    if not _url.startswith(("http://", "https://")):
        _url = f"https://{_url}"
    LOCAL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_CONFIG_PATH.write_text(json.dumps({"app_url": _url}))


def refresh_replica() -> Path:
    """Download a consistent snapshot of the ledger and atomically replace the local replica."""
    db_path = REPLICA_PATH
    app_url = get_app_url()
    if app_url is None:
        raise RefreshError("no server address is configured yet")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = db_path.with_suffix(".tmp")
    try:
        # app_url comes from the user's local config (set_app_url), not a remote party
        with urllib.request.urlopen(  # noqa: S310 - scheme is whatever the user configured locally
            f"{app_url}/api/analytics/db-snapshot",
            timeout=60,
        ) as resp:
            if resp.status != 200:
                raise RefreshError(f"snapshot download returned HTTP {resp.status}")
            with tmp_path.open("wb") as f:
                while chunk := resp.read(65536):
                    f.write(chunk)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        tmp_path.unlink(missing_ok=True)
        raise RefreshError(f"failed to download ledger snapshot: {exc}") from exc

    with tmp_path.open("rb") as f:
        is_valid_sqlite = f.read(len(_SQLITE_MAGIC_HEADER)) == _SQLITE_MAGIC_HEADER
    if not is_valid_sqlite:
        tmp_path.unlink(missing_ok=True)
        raise RefreshError("downloaded snapshot is not a valid SQLite database")

    os.replace(tmp_path, db_path)
    return db_path


def _refresh_loop() -> None:
    global _db_path, _last_refresh, _last_refresh_error  # noqa: PLW0603
    retry_delay = REFRESH_RETRY_BASE_SECONDS
    while True:
        try:
            path = refresh_replica()
        except RefreshError as exc:
            logger.warning("ledger replica refresh failed: %s", exc)
            with _lock:
                _last_refresh_error = str(exc)
            _wake_event.wait(timeout=retry_delay)
            _wake_event.clear()
            retry_delay = min(retry_delay * 2, REFRESH_INTERVAL_SECONDS)
        else:
            with _lock:
                _db_path = path
                _last_refresh = time.time()
                _last_refresh_error = None
            retry_delay = REFRESH_RETRY_BASE_SECONDS
            _wake_event.wait(timeout=REFRESH_INTERVAL_SECONDS)
            _wake_event.clear()


def trigger_refresh_now() -> None:
    """Wake the refresh loop so it refreshes immediately instead of waiting out its timer."""
    _wake_event.set()


def get_db_path() -> Path | None:
    """Return the path of the most recently successfully refreshed replica, or None."""
    with _lock:
        return _db_path


def get_last_refresh() -> float | None:
    """Return the ``time.time()`` timestamp of the last successful refresh, or None."""
    with _lock:
        return _last_refresh


def get_last_refresh_error() -> str | None:
    """Return the message of the most recent refresh error, or None if the last one succeeded."""
    with _lock:
        return _last_refresh_error


def start_refresh_daemon() -> None:
    """Spawn the background refresh thread, unless one is already running."""
    global _daemon_thread  # noqa: PLW0603
    if _daemon_thread is not None and _daemon_thread.is_alive():
        return
    _daemon_thread = threading.Thread(target=_refresh_loop, daemon=True)
    _daemon_thread.start()
