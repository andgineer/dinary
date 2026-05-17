"""Google API client singletons: gspread (Sheets) and Drive credentials."""

import threading

import google.auth.transport.requests as _google_requests
import gspread
import httpx
from google.oauth2.service_account import Credentials

from dinary.config import settings

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
# Drive metadata access uses a separate credential scoped to read-only Drive
# metadata so a leaked gspread token cannot read file listings elsewhere.
_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.metadata.readonly"]

_gc: gspread.Client | None = None
# Guards lazy init against concurrent first-touch: two threads racing can both
# find _gc=None and both parse the service-account JSON. The lock prevents the
# redundant work; last-write-wins is still correct.
_client_lock = threading.Lock()


def _get_client() -> gspread.Client:
    global _gc  # noqa: PLW0603
    if _gc is not None:
        return _gc
    with _client_lock:
        if _gc is None:
            creds = Credentials.from_service_account_file(
                str(settings.google_sheets_credentials_path),
                scopes=SCOPES,
            )
            _gc = gspread.authorize(creds)
    return _gc


def get_sheet(spreadsheet_id: str) -> gspread.Spreadsheet:
    return _get_client().open_by_key(spreadsheet_id)


_drive_creds: Credentials | None = None
_drive_creds_lock = threading.Lock()


def _drive_credentials() -> Credentials:
    """Lazy-init Drive-only credentials; refresh() is not inside the lock.

    Token refresh is safe to call concurrently (google-auth documents this).
    Holding the lock across a network round-trip would serialise every drain
    sweep behind every admin call.
    """
    global _drive_creds  # noqa: PLW0603
    if _drive_creds is None:
        with _drive_creds_lock:
            if _drive_creds is None:
                _drive_creds = Credentials.from_service_account_file(
                    str(settings.google_sheets_credentials_path),
                    scopes=_DRIVE_SCOPES,
                )
    if not _drive_creds.valid:
        _drive_creds.refresh(_google_requests.Request())
    return _drive_creds


def drive_get_modified_time(spreadsheet_id: str) -> str:
    """Return the spreadsheet's ``modifiedTime`` as an RFC3339 UTC string.

    Raises ``httpx.HTTPStatusError`` on non-2xx so the caller can decide
    whether to bail or reload eagerly.
    """
    creds = _drive_credentials()
    url = f"https://www.googleapis.com/drive/v3/files/{spreadsheet_id}"
    headers = {"Authorization": f"Bearer {creds.token}"}
    params = {"fields": "modifiedTime", "supportsAllDrives": "true"}
    with httpx.Client(timeout=10.0) as client:
        resp = client.get(url, headers=headers, params=params)
    resp.raise_for_status()
    body = resp.json()
    modified_time = body.get("modifiedTime")
    if not isinstance(modified_time, str):
        msg = f"Drive API returned no modifiedTime for {spreadsheet_id!r}: {body!r}"
        raise TypeError(msg)
    return modified_time
