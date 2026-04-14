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


class Settings(BaseSettings):
    model_config = {"env_prefix": "DINARY_"}

    google_sheets_credentials_path: Path = Path("credentials.json")
    google_sheets_spreadsheet_id: str = ""

    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"
    log_json: bool = False


settings = Settings()
_materialize_b64_credentials(settings.google_sheets_credentials_path)
