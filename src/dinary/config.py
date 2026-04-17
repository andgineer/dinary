import base64
import os
from pathlib import Path

from pydantic_settings import BaseSettings


def _materialize_b64_credentials(target: Path) -> None:
    """Decode DINARY_GOOGLE_CREDENTIALS_BASE64 env var into a JSON file on disk.

    Useful on platforms without secret-file support (e.g. Railway).
    """
    b64 = os.getenv("DINARY_GOOGLE_CREDENTIALS_BASE64")
    if b64 and not target.exists():
        target.write_bytes(base64.b64decode(b64))


_GSPREAD_DEFAULT = Path.home() / ".config" / "gspread" / "service_account.json"


class Settings(BaseSettings):
    model_config = {"env_prefix": "DINARY_", "env_file": ".env", "extra": "ignore"}

    google_sheets_credentials_path: Path = _GSPREAD_DEFAULT
    google_sheets_spreadsheet_id: str = ""
    sheet_import_sources_json: str = ""

    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8000
    log_level: str = "info"
    log_json: bool = False


settings = Settings()
_materialize_b64_credentials(settings.google_sheets_credentials_path)
