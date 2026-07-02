"""Environment / deploy-host readers for task modules."""

import sys
from pathlib import Path

from dotenv import dotenv_values

from .constants import (
    LOCAL_ENV_EXAMPLE_PATH,
    LOCAL_ENV_PATH,
    VALID_TUNNELS,
)


def _env():
    """Reads ``.deploy/.env``. Fails fast (with an actionable message) if the file
    is missing, empty, or byte-equal to the template — an unedited template ships
    placeholder values (``ubuntu@<PUBLIC_IP>``) to prod and fails at SSH time with
    an opaque DNS error otherwise."""
    local_path = Path(LOCAL_ENV_PATH)
    if not local_path.exists():
        print(
            f"Missing {LOCAL_ENV_PATH}. Copy {LOCAL_ENV_EXAMPLE_PATH} to {LOCAL_ENV_PATH} "
            "and fill in DINARY_DEPLOY_HOST / DINARY_TUNNEL / any sheet-logging "
            "settings you need.",
        )
        sys.exit(1)
    local_bytes = local_path.read_bytes()
    if not local_bytes.strip():
        print(
            f"{LOCAL_ENV_PATH} is empty. Fill in DINARY_DEPLOY_HOST / DINARY_TUNNEL / "
            "any sheet-logging settings you need (see "
            f"{LOCAL_ENV_EXAMPLE_PATH} for the template).",
        )
        sys.exit(1)
    example_path = Path(LOCAL_ENV_EXAMPLE_PATH)
    if example_path.exists() and local_bytes == example_path.read_bytes():
        print(
            f"{LOCAL_ENV_PATH} is byte-equal to {LOCAL_ENV_EXAMPLE_PATH}; the "
            "template still has placeholder values (e.g. ubuntu@<PUBLIC_IP>) "
            "that would ship to prod and break the deploy. Edit "
            f"{LOCAL_ENV_PATH} with your real values before continuing.",
        )
        sys.exit(1)
    return dotenv_values(LOCAL_ENV_PATH)


def bind_host(tunnel: str) -> str:
    """``tailscale``/``cloudflare`` bind to loopback so the proxy can reach them
    (``tailscale serve`` proxies to ``127.0.0.1`` specifically — binding the
    Tailscale IP instead breaks HTTPS and forces clients onto plain HTTP).
    ``none`` binds the public interface directly."""
    if tunnel == "none":
        return "0.0.0.0"  # noqa: S104
    return "127.0.0.1"


def host():
    h = _env().get("DINARY_DEPLOY_HOST")
    if not h:
        print("Set DINARY_DEPLOY_HOST in .env  (e.g. ubuntu@1.2.3.4)")
        sys.exit(1)
    return h


def replica_host():
    """Separate from :func:`_host`: the replica is a distinct VM that must never
    receive ``inv deploy``, so a typo can't accidentally target the wrong one."""
    h = _env().get("DINARY_REPLICA_HOST")
    if not h:
        print(
            "Set DINARY_REPLICA_HOST in .deploy/.env  (e.g. ubuntu@dinary-replica)",
        )
        sys.exit(1)
    return h


def litestream_retention() -> str:
    """Defaults to ``168h`` (7 days) when unset, so existing deployments are unaffected."""
    return _env().get("DINARY_LITESTREAM_RETENTION") or "168h"


def tunnel():
    tunnel = (_env().get("DINARY_TUNNEL") or "tailscale").lower()
    if tunnel not in VALID_TUNNELS:
        print(f"DINARY_TUNNEL must be one of: {', '.join(VALID_TUNNELS)}")
        sys.exit(1)
    return tunnel
