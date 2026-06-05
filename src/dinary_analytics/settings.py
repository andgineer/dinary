"""analytics.db key-value store backed by LMDB."""

import json
import uuid
from pathlib import Path

import lmdb

from dinary_analytics.connection import ANALYTICS_DB_PATH

_MAP_SIZE = 10 * 1024 * 1024  # 10 MB


def _open_env(db_path: Path) -> lmdb.Environment:
    db_path.mkdir(parents=True, exist_ok=True)
    return lmdb.open(str(db_path), map_size=_MAP_SIZE, create=True)


def get_config(key: str, db_path: Path | None = None) -> str | None:
    """Return the config value for key, or None if not set."""
    path = db_path or ANALYTICS_DB_PATH
    with _open_env(path) as env, env.begin() as txn:
        raw = txn.get(key.encode())
        return bytes(raw).decode() if raw is not None else None


def set_config(key: str, value: str, db_path: Path | None = None) -> None:
    """Write a config value to analytics.db."""
    path = db_path or ANALYTICS_DB_PATH
    with _open_env(path) as env, env.begin(write=True) as txn:
        txn.put(key.encode(), value.encode())


def get_config_json(key: str, db_path: Path | None = None) -> object:
    """Return a JSON-decoded config value, or None if not set."""
    raw = get_config(key, db_path)
    return json.loads(raw) if raw is not None else None


def set_config_json(key: str, value: object, db_path: Path | None = None) -> None:
    """JSON-encode and store a config value."""
    set_config(key, json.dumps(value), db_path)


def list_view_ids(db_path: Path | None = None) -> list[str]:
    """Return IDs of all saved analytics views (keys starting with 'view:')."""
    path = db_path or ANALYTICS_DB_PATH
    prefix = b"view:"
    ids: list[str] = []
    with _open_env(path) as env, env.begin() as txn:
        cursor = txn.cursor()
        found = cursor.set_range(prefix)
        while found:
            key = bytes(cursor.key())
            if not key.startswith(prefix):
                break
            ids.append(key[len(prefix) :].decode())
            found = cursor.next()
    return ids


def get_view(view_id: str, db_path: Path | None = None) -> dict | None:
    """Return the view config dict for view_id, or None if not found."""
    raw = get_config("view:" + view_id, db_path)
    return json.loads(raw) if raw is not None else None


def save_view(config: dict, db_path: Path | None = None) -> str:
    """Save a view config dict. Assigns a UUID if config has no 'id'. Returns the view ID."""
    view_id = config.get("id") or str(uuid.uuid4())
    config = {**config, "id": view_id}
    set_config("view:" + view_id, json.dumps(config), db_path)
    return view_id


def delete_view(view_id: str, db_path: Path | None = None) -> None:
    """Delete a saved view by ID. No-op if not found."""
    path = db_path or ANALYTICS_DB_PATH
    with _open_env(path) as env, env.begin(write=True) as txn:
        txn.delete(("view:" + view_id).encode())
