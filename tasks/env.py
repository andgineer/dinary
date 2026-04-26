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
    """Read runtime env vars from ``.deploy/.env`` (the post-refactor canonical path).

    The legacy top-level ``.env`` is deliberately no longer consulted:
    it was removed in the same change that introduced
    ``.deploy/.env``, and keeping a silent fallback would make
    mis-scoped env vars hard to spot. ``.env.example`` has also been
    deleted in favour of ``.deploy.example/.env``.

    Sanity checks beyond "file exists" — the file must also be
    non-empty and not byte-equal to ``.deploy.example/.env``. Both
    failure modes are operator mistakes that would otherwise propagate
    silently: an empty ``.deploy/.env`` produces "No DINARY_* settings
    found" deep inside ``_sync_remote_env``, and an unedited copy of
    the template ships placeholder values (``ubuntu@<PUBLIC_IP>``) to
    prod, which then fail at SSH time with an opaque DNS error. Fail
    fast here with an actionable message instead.
    """
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
    """Return the ``--host`` value ``uvicorn`` should bind to.

    Tunnel ``none`` exposes the service directly on the public
    interface; ``tailscale`` / ``cloudflare`` front it so we stay on
    loopback. Shared by ``setup`` and ``deploy`` so both paths render
    the same ``DINARY_SERVICE`` unit file.
    """
    if tunnel == "none":
        return "0.0.0.0"  # noqa: S104
    if tunnel == "tailscale":
        return "$(tailscale ip -4 2>/dev/null || echo 127.0.0.1)"
    return "127.0.0.1"


def host():
    h = _env().get("DINARY_DEPLOY_HOST")
    if not h:
        print("Set DINARY_DEPLOY_HOST in .env  (e.g. ubuntu@1.2.3.4)")
        sys.exit(1)
    return h


def replica_host():
    """Read the Litestream replica host (VM2) from ``.deploy/.env``.

    Separate from :func:`_host` because the replica is a distinct VM
    with its own MagicDNS/Tailscale identity, owns no Python app, and
    must never receive ``inv deploy``. Keeping the two hosts in
    independent env vars makes it impossible for a typo in one to
    accidentally target the other.
    """
    h = _env().get("DINARY_REPLICA_HOST")
    if not h:
        print(
            "Set DINARY_REPLICA_HOST in .deploy/.env  (e.g. ubuntu@dinary-replica)",
        )
        sys.exit(1)
    return h


def tunnel():
    tunnel = (_env().get("DINARY_TUNNEL") or "tailscale").lower()
    if tunnel not in VALID_TUNNELS:
        print(f"DINARY_TUNNEL must be one of: {', '.join(VALID_TUNNELS)}")
        sys.exit(1)
    return tunnel
