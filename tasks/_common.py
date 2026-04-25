"""Shared constants, imports, and SSH/env helpers for all task modules."""

import base64
import json as _json
import re as _re
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values


def get_allowed_doc_languages():
    build_docs_file_name = "scripts/build-docs.sh"
    try:
        with open(build_docs_file_name) as f:
            for line in f:
                if "LANGUAGES=" in line:
                    value = line.split("=", 1)[1].strip()
                    value = value.strip('"').strip("'")
                    langs = value.split()
                    return [lang.strip() for lang in langs]
    except FileNotFoundError:
        print(f"No {build_docs_file_name} file found")
    return ["en", "bg", "de", "es", "fr", "ru"]


ALLOWED_DOC_LANGUAGES = get_allowed_doc_languages()
ALLOWED_VERSION_TYPES = ["release", "bug", "feature"]

REPO_URL = "https://github.com/andgineer/dinary.git"

DINARY_SERVICE = """\
[Unit]
Description=dinary
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/dinary
EnvironmentFile=/home/ubuntu/dinary/.deploy/.env
ExecStart=/home/ubuntu/.local/bin/uv run uvicorn dinary.main:app --host {host} --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""

CLOUDFLARED_SERVICE = """\
[Unit]
Description=Cloudflare Tunnel for dinary
After=network.target

[Service]
Type=simple
User=ubuntu
ExecStart=/usr/bin/cloudflared tunnel run
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""

# systemd unit that runs the Litestream replicator sidecar on VM 1.
# ``User=ubuntu`` matches the app service so the sidecar shares the
# ``data/`` file ACL and can read WAL segments without sudo. The config
# lives at ``/etc/litestream.yml`` (uploaded by ``inv litestream-setup``)
# because that is Litestream's default search path and operators editing
# by hand find it where the upstream docs say it should be.
LITESTREAM_SERVICE = """\
[Unit]
Description=Litestream replicator for dinary
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
ExecStart=/usr/bin/litestream replicate -config /etc/litestream.yml
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""

VALID_TUNNELS = ("tailscale", "cloudflare", "none")

LOCAL_ENV_PATH = ".deploy/.env"
LOCAL_ENV_EXAMPLE_PATH = ".deploy.example/.env"
LOCAL_IMPORT_SOURCES_PATH = ".deploy/import_sources.json"
LOCAL_LITESTREAM_CONFIG_PATH = ".deploy/litestream.yml"
LOCAL_LITESTREAM_EXAMPLE_PATH = ".deploy.example/litestream.yml"
REMOTE_DEPLOY_DIR = "/home/ubuntu/dinary/.deploy"
REMOTE_ENV_PATH = f"{REMOTE_DEPLOY_DIR}/.env"
REMOTE_LEGACY_ENV_PATH = "/home/ubuntu/dinary/.env"
REMOTE_IMPORT_SOURCES_PATH = f"{REMOTE_DEPLOY_DIR}/import_sources.json"
REMOTE_LITESTREAM_CONFIG_PATH = "/etc/litestream.yml"

# Pinned Litestream release we install on VM 1. Keeping this as a
# module-level constant — rather than inlined into the shell script
# below — lets tests pin the exact version independently of the
# asset-URL construction, and makes a future upgrade a one-line
# diff that cannot get out of sync with the asset filenames.
LITESTREAM_VERSION = "0.5.1"

REPLICA_LITESTREAM_DIR = "/var/lib/litestream"

# Directory Litestream materializes inside REPLICA_LITESTREAM_DIR for
# our single ``dinary.db``. Matches the trailing segment of the
# ``path:`` field in .deploy/litestream.yml — a silent drift here
# would make ``inv backup-cloud-setup`` restore from the wrong replica tree.
REPLICA_DB_NAME = "dinary"

# Off-site backup to Yandex.Disk (see docs/src/en/operations.md,
# section "Off-site backup: Yandex.Disk"). Everything on the replica
# is managed by ``inv backup-cloud-setup``; the restore side is
# ``inv backup-cloud-restore`` (local-only, runs in cwd).
BACKUP_SCRIPT_PATH = "/usr/local/bin/dinary-backup"
BACKUP_RETENTION_SCRIPT_PATH = "/usr/local/bin/dinary-backup-retention"
BACKUP_SERVICE_PATH = "/etc/systemd/system/dinary-backup.service"
BACKUP_TIMER_PATH = "/etc/systemd/system/dinary-backup.timer"
BACKUP_RCLONE_REMOTE = "yandex"
# Nested under ``Backup/`` because the operator's Yandex.Disk already
# has a ``Backup`` folder used by other tools — keeping dinary under a
# leaf ``dinary/`` avoids colliding with ad-hoc human uploads in the
# same namespace and matches the existing filesystem convention.
# ``rclone mkdir`` is idempotent and will create the ``dinary`` leaf
# inside the existing ``Backup`` parent on first run.
BACKUP_RCLONE_PATH = "Backup/dinary"
BACKUP_FILENAME_PREFIX = "dinary-"
BACKUP_FILENAME_SUFFIX = ".db.zst"

# GFS retention. Closed years are immutable, so ``yearly`` is kept
# indefinitely — every closed-year snapshot is ~300 KB compressed and
# any drift between two yearly snapshots of the same closed year is a
# corruption signal worth retaining forever.
BACKUP_RETENTION_DAILY = 7
BACKUP_RETENTION_WEEKLY = 4
BACKUP_RETENTION_MONTHLY = 12

# Freshness threshold for `inv backup-cloud-status`. The daily systemd timer
# fires at 03:17 UTC with 30 min jitter, so a healthy snapshot is
# always <= 24h30m old. 26h gives a ~1h30m buffer for a single missed
# jitter window without false-alerting. A stale snapshot for >26h
# means the pipeline silently stopped producing.
BACKUP_STALE_HOURS = 26

#: Remote path of the live SQLite ledger file.
_REMOTE_DB_PATH = "/home/ubuntu/dinary/data/dinary.db"


def _litestream_install_script(version: str = LITESTREAM_VERSION) -> str:
    """Return the shell snippet that installs Litestream on the VM.

    Extracted as a pure helper so ``tests/test_tasks.py`` can assert
    the arch-detection branches without booting an SSH session.

    The filename suffix contract comes from the upstream release page:
    assets are published as ``litestream-<version>-linux-x86_64.deb``
    and ``litestream-<version>-linux-arm64.deb``. We map ``uname -m``
    outputs (``x86_64`` / ``amd64`` on Intel-style hosts, ``aarch64``
    / ``arm64`` on Ampere) to those canonical suffixes. Other
    architectures exit 1 with an actionable error instead of silently
    downloading a non-existent asset.
    """
    base_url = f"https://github.com/benbjohnson/litestream/releases/download/v{version}"
    return (
        "set -e; "
        "if ! command -v litestream >/dev/null; then "
        'ARCH="$(uname -m)"; '
        'case "$ARCH" in '
        f"  x86_64|amd64) ASSET=litestream-{version}-linux-x86_64.deb ;; "
        f"  aarch64|arm64) ASSET=litestream-{version}-linux-arm64.deb ;; "
        f'  *) echo "Unsupported arch $ARCH for litestream {version}" >&2; exit 1 ;; '
        "esac; "
        "TMP=$(mktemp -d); "
        "curl -fsSL -o $TMP/litestream.deb "
        f'"{base_url}/$ASSET"; '
        "sudo dpkg -i $TMP/litestream.deb; "
        "rm -rf $TMP; "
        "fi"
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


def _bind_host(tunnel: str) -> str:
    """Return the ``--host`` value ``uvicorn`` should bind to.

    Tunnel ``none`` exposes the service directly on the public
    interface; ``tailscale`` / ``cloudflare`` front it so we stay on
    loopback. Shared by ``setup`` and ``deploy`` so both paths render
    the same ``DINARY_SERVICE`` unit file.
    """
    return "0.0.0.0" if tunnel == "none" else "127.0.0.1"  # noqa: S104


def _host():
    host = _env().get("DINARY_DEPLOY_HOST")
    if not host:
        print("Set DINARY_DEPLOY_HOST in .env  (e.g. ubuntu@1.2.3.4)")
        sys.exit(1)
    return host


def _replica_host():
    """Read the Litestream replica host (VM2) from ``.deploy/.env``.

    Separate from :func:`_host` because the replica is a distinct VM
    with its own MagicDNS/Tailscale identity, owns no Python app, and
    must never receive ``inv deploy``. Keeping the two hosts in
    independent env vars makes it impossible for a typo in one to
    accidentally target the other.
    """
    host = _env().get("DINARY_REPLICA_HOST")
    if not host:
        print(
            "Set DINARY_REPLICA_HOST in .deploy/.env  (e.g. ubuntu@dinary-replica)",
        )
        sys.exit(1)
    return host


def _tunnel():
    tunnel = (_env().get("DINARY_TUNNEL") or "tailscale").lower()
    if tunnel not in VALID_TUNNELS:
        print(f"DINARY_TUNNEL must be one of: {', '.join(VALID_TUNNELS)}")
        sys.exit(1)
    return tunnel


def _ssh(c, cmd):
    b64 = base64.b64encode(cmd.encode()).decode()
    c.run(f"ssh {_host()} 'echo {b64} | base64 -d | bash'")


def _ssh_replica(c, cmd):
    """Run *cmd* on the Litestream replica (VM2) via SSH.

    Mirrors :func:`_ssh` but targets :func:`_replica_host` so the
    replica bootstrap path cannot be silently redirected at the app
    server, and vice versa.
    """
    b64 = base64.b64encode(cmd.encode()).decode()
    c.run(f"ssh {_replica_host()} 'echo {b64} | base64 -d | bash'")


def _ssh_replica_capture_bytes(cmd: str) -> bytes:
    """Run *cmd* on the replica (VM2) and return its stdout as bytes.

    Mirrors :func:`_ssh_capture_bytes` but targets :func:`_replica_host`.
    Used by :func:`backup_status` to ask VM2's ``rclone`` for the
    off-site inventory over SSH, so the laptop does not need a
    ``yandex:`` remote of its own just to monitor freshness.
    """
    b64 = base64.b64encode(cmd.encode()).decode()
    remote = f"echo {b64} | base64 -d | bash"
    result = subprocess.run(
        ["ssh", _replica_host(), remote],
        stdout=subprocess.PIPE,
        check=True,
    )
    return result.stdout


def _ssh_sudo(c, cmd):
    _ssh(c, f"sudo {cmd}")


def _ssh_capture_bytes(cmd: str) -> bytes:
    """Run *cmd* over SSH and return its stdout as raw bytes.

    Uses ``subprocess.run`` directly (not ``invoke.Context.run``) so
    the UTF-8 decode is a single end-of-stream call on the caller's
    side rather than a per-chunk ``decode(..., errors='replace')``.
    That matters because :meth:`invoke.runners.Runner.decode` splits
    decoding along read-buffer boundaries; any multi-byte character
    that lands on one becomes ``U+FFFD`` (the ``\ufffd`` replacement
    glyph), which corrupts Cyrillic text and box-drawing glyphs.

    Stderr is inherited so remote tracebacks / ``uv run`` notices
    still surface live.

    Transport shape matches the other ``_ssh*`` helpers: the command
    is base64-encoded and piped through ``base64 -d | bash`` on the
    remote, so single-quote-bearing payloads need no extra escaping.
    """
    b64 = base64.b64encode(cmd.encode()).decode()
    remote = f"echo {b64} | base64 -d | bash"
    result = subprocess.run(
        ["ssh", _host(), remote],
        stdout=subprocess.PIPE,
        check=True,
    )
    return result.stdout


def _ssh_capture(c, cmd):  # noqa: ARG001
    """Run *cmd* over SSH and return an ``invoke.Result``-shaped object.

    Thin adapter over :func:`_ssh_capture_bytes` exposing
    ``.stdout`` (already-decoded text) and ``.return_code``. New
    callers should prefer :func:`_ssh_capture_bytes` directly and
    decode when they actually need text.
    """
    stdout = _ssh_capture_bytes(cmd)

    class _Result:
        def __init__(self, stdout_bytes: bytes) -> None:
            self.stdout = stdout_bytes.decode("utf-8")
            self.return_code = 0

    return _Result(stdout)


def _ssh_json(c, cmd):  # noqa: ARG001
    """Run *cmd* on the server and return the JSON it printed to stdout.

    On parse failure the raw stdout is echoed back to local stderr
    (so a Python traceback from the remote payload stays visible)
    before raising ``RuntimeError``.
    """
    raw = _ssh_capture_bytes(cmd)
    try:
        return _json.loads(raw.decode("utf-8"))
    except _json.JSONDecodeError as exc:
        sys.stderr.write(
            "remote did not return valid JSON on stdout; raw stdout follows:\n",
        )
        sys.stderr.buffer.write(raw)
        if not raw.endswith(b"\n"):
            sys.stderr.write("\n")
        msg = f"remote command failed to emit JSON: {exc.msg} at pos {exc.pos}"
        raise RuntimeError(msg) from exc


def _write_remote_file(c, path, content):
    b64 = base64.b64encode(content.encode()).decode()
    c.run(f"ssh {_host()} 'echo {b64} | base64 -d | sudo tee {path} > /dev/null'")


def _write_remote_replica_file(c, path, content):
    """Like :func:`_write_remote_file` but targets the replica (VM2).

    Mirror of the app-server helper, split so the replica-bootstrap
    path cannot be silently redirected at VM1 (and vice versa). Used
    by :func:`setup_replica_backup` to install ``/usr/local/bin/dinary-backup``
    and the paired systemd units on the replica.
    """
    b64 = base64.b64encode(content.encode()).decode()
    c.run(
        f"ssh {_replica_host()} 'echo {b64} | base64 -d | sudo tee {path} > /dev/null'",
    )


_ENV_SAFE_RE = _re.compile(r"^[A-Za-z0-9_./:@\-]*$")


def _systemd_quote(value: str | None) -> str:
    r"""Quote a value for systemd's ``EnvironmentFile=`` parser.

    systemd uses shell-like double-quote semantics: bare values may not
    contain spaces, quotes, or special chars; quoted values back-slash
    escape ``\``, ``"``, and ``$`` as in POSIX shell. We wrap any value
    containing unsafe characters in double quotes so JSON, base64 blobs
    with ``+``, and URLs with ``=``/``?`` round-trip correctly even
    though ``dotenv_values()`` strips the surrounding quotes locally.
    """
    if not value:
        return ""
    if _ENV_SAFE_RE.match(value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
    return f'"{escaped}"'


def _render_service(c, name, content):
    """Install / refresh a systemd unit file without restarting the service.

    Used by ``deploy`` so the unit-file content always reflects
    ``DINARY_SERVICE`` / ``CLOUDFLARED_SERVICE`` as they live in this
    repo (``git pull`` never touches ``/etc/systemd/system/*``), while
    the restart stays gated by the existing ``--no-restart`` flag
    further down the pipeline. ``daemon-reload`` here is what lets the
    follow-up ``systemctl restart`` pick up the new
    ``EnvironmentFile=`` / ``ExecStart=`` lines.
    """
    _write_remote_file(c, f"/etc/systemd/system/{name}.service", content)
    _ssh_sudo(c, "systemctl daemon-reload")
    _ssh_sudo(c, f"systemctl enable {name}")


def _create_service(c, name, content):
    """Render a systemd unit and restart it (used during ``inv setup``)."""
    _render_service(c, name, content)
    _ssh_sudo(c, f"systemctl restart {name}")


def _sync_remote_env(c):
    """Sync all DINARY_* settings from local .deploy/.env to the server (idempotent).

    Skips deploy-only keys (DINARY_DEPLOY_HOST, DINARY_TUNNEL) that are
    only meaningful on the operator's machine. Values are quoted using
    systemd's ``EnvironmentFile=`` syntax so base64-encoded credentials
    survive the transfer intact. The remote file is also chowned to
    ``ubuntu`` so the systemd unit (``User=ubuntu``) can read it.
    """
    env = _env()
    skip = {"DINARY_DEPLOY_HOST", "DINARY_TUNNEL"}
    lines = [
        f"{k}={_systemd_quote(v)}\n"
        for k, v in env.items()
        if k.startswith("DINARY_") and k not in skip
    ]
    if not lines:
        print(f"No DINARY_* settings found in local {LOCAL_ENV_PATH}")
        sys.exit(1)
    _ssh(c, f"mkdir -p {REMOTE_DEPLOY_DIR}")
    _write_remote_file(c, REMOTE_ENV_PATH, "".join(lines))
    _ssh_sudo(c, f"chown ubuntu:ubuntu {REMOTE_ENV_PATH} && chmod 600 {REMOTE_ENV_PATH}")


def _sync_remote_import_sources(c):
    """Upload optional ``.deploy/import_sources.json`` to the server.

    Skipped silently when the local file is absent — non-import
    deployments have no source list. Present uploads overwrite the
    remote copy so operator edits always round-trip through the
    repo-local file (single source of truth).
    """
    if not Path(LOCAL_IMPORT_SOURCES_PATH).exists():
        print(f"No {LOCAL_IMPORT_SOURCES_PATH} locally; skipping import-sources sync.")
        return
    _ssh(c, f"mkdir -p {REMOTE_DEPLOY_DIR}")
    content = Path(LOCAL_IMPORT_SOURCES_PATH).read_text(encoding="utf-8")
    _write_remote_file(c, REMOTE_IMPORT_SOURCES_PATH, content)
    _ssh_sudo(
        c,
        f"chown ubuntu:ubuntu {REMOTE_IMPORT_SOURCES_PATH} "
        f"&& chmod 600 {REMOTE_IMPORT_SOURCES_PATH}",
    )


def _setup_tailscale(c):
    print("=== Installing Tailscale ===")
    _ssh(c, "command -v tailscale || curl -fsSL https://tailscale.com/install.sh | sh")
    _ssh_sudo(c, "tailscale up")

    print("=== Enabling Tailscale Serve (tailnet only) ===")
    _ssh_sudo(
        c,
        "tailscale serve reset 2>/dev/null; tailscale funnel reset 2>/dev/null; "
        "tailscale serve --bg 8000",
    )


def _setup_cloudflare(c):
    print("=== Installing cloudflared ===")
    _ssh(
        c,
        "command -v cloudflared || "
        "(curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/"
        "cloudflared-linux-amd64 "
        "-o /tmp/cloudflared && sudo install /tmp/cloudflared /usr/bin/cloudflared)",
    )
    _ssh(c, "cloudflared tunnel login")

    print("=== Creating cloudflared service ===")
    _create_service(c, "cloudflared", CLOUDFLARED_SERVICE)


def _build_setup_swap_script(*, size_gb: int) -> str:
    """Emit the shell script that idempotently installs ``/swapfile``.

    Kept separate from :func:`setup_swap` so tests can pin the shape
    of the emitted script (fallocate size, idempotency guard, fstab
    dedup) without mocking SSH.

    Uses ``sudo bash <<'HEREDOC'`` so every command inside the
    block runs as root without nested-quote gymnastics. The
    heredoc is quoted (``<<'EOF'``) so ``$FSTAB_LINE`` stays
    literal for the remote shell instead of being expanded locally
    to an empty string.
    """
    if size_gb <= 0:
        msg = f"size_gb must be a positive integer, got {size_gb!r}"
        raise ValueError(msg)
    return (
        "sudo bash <<'DINARY_SWAP_EOF'\n"
        "set -euo pipefail\n"
        "if swapon --show=NAME --noheadings 2>/dev/null | grep -qx /swapfile; then\n"
        '  echo "/swapfile already active, skipping allocation"\n'
        "else\n"
        f"  fallocate -l {size_gb}G /swapfile\n"
        "  chmod 600 /swapfile\n"
        "  mkswap /swapfile\n"
        "  swapon /swapfile\n"
        "fi\n"
        "FSTAB_LINE='/swapfile none swap sw 0 0'\n"
        'grep -qxF "$FSTAB_LINE" /etc/fstab || echo "$FSTAB_LINE" >> /etc/fstab\n'
        "swapon --show\n"
        "free -h\n"
        "DINARY_SWAP_EOF\n"
    )


def _build_ssh_tailscale_only_script() -> str:
    """Emit the shell script that restricts ``sshd`` ingress to Tailscale.

    Kept separate from :func:`ssh_tailscale_only` so tests can pin the
    script's observable contract (pre-flight Tailscale check, atomic
    ``sshd -t`` validation with rollback, idempotent drop-in file)
    without mocking SSH.

    Shape and safety properties worth pinning explicitly:
    - The drop-in file is always rewritten from scratch using a fresh
      ``tailscale ip -4`` read off the remote, so a re-run after a
      Tailscale IP rotation self-heals instead of leaving the old
      ``ListenAddress`` line in place.
    - ``ListenAddress 127.0.0.1:22`` is kept so loopback ssh (the
      last-resort path from inside the box over the Serial Console)
      still works after the flip.
    - ``sshd -t`` validates the merged config *before* any reload; on
      failure we delete the drop-in so a broken file cannot persist
      across reboot and lock the operator out.
    - ``tailscale`` must be installed and ``tailscale ip -4`` must
      return a non-empty IPv4; otherwise we would bind sshd to nothing
      and effectively kill inbound SSH.
    - The outer ``sudo bash <<'HEREDOC'`` elevates the whole block in
      one call — all of the file write, validation, rollback, reload
      and status probe must run as root.
    """
    return (
        "sudo bash <<'DINARY_SSH_TS_EOF'\n"
        "set -euo pipefail\n"
        "if ! command -v tailscale >/dev/null 2>&1; then\n"
        '  echo "tailscale is not installed; refusing to rebind sshd" >&2\n'
        "  exit 1\n"
        "fi\n"
        'TS_IP="$(tailscale ip -4 2>/dev/null | head -1)"\n'
        'if [ -z "$TS_IP" ]; then\n'
        '  echo "tailscaled is not up (no IPv4); refusing to rebind sshd" >&2\n'
        "  exit 1\n"
        "fi\n"
        "DROPIN=/etc/ssh/sshd_config.d/10-tailscale-only.conf\n"
        'cat >"$DROPIN" <<EOC\n'
        "# Managed by inv ssh-tailscale-only. Overwritten on every run.\n"
        "# Closes public TCP/22 by binding sshd to the Tailscale IPv4\n"
        "# plus loopback; break-glass is via Oracle Cloud Serial Console.\n"
        "ListenAddress ${TS_IP}:22\n"
        "ListenAddress 127.0.0.1:22\n"
        "EOC\n"
        "if ! sshd -t; then\n"
        '  echo "sshd -t rejected the new config; rolling back" >&2\n'
        '  rm -f "$DROPIN"\n'
        "  exit 1\n"
        "fi\n"
        "systemctl reload ssh\n"
        "ss -tlnp | awk '/:22 / {print}'\n"
        "DINARY_SSH_TS_EOF\n"
    )


def _sqlite_backup_to_tmp_snapshot_prologue(snap_prefix: str) -> str:
    """Return the common prologue that snapshots ``_REMOTE_DB_PATH`` to ``/tmp``.

    Four call sites (``inv deploy`` pre-backup, ``inv backup``,
    ``inv verify-db --remote``, and ``_remote_snapshot_cmd`` for the
    report tasks) all need the same shape: enable ``set -e``, stake
    out a per-invocation ``/tmp`` path in ``$SNAP``, register a
    ``trap`` so the snapshot is torn down on every exit path
    (success, failure, ``SIGINT``), and then invoke
    ``sqlite3 "$DB" ".backup \\"$SNAP\\""`` to materialize a
    transactionally consistent copy of the live DB.

    The trap is deliberately registered **before** the
    ``sqlite3 .backup`` so an interrupt between the two cannot leak
    the snapshot file. ``$$`` expands to the remote shell's PID so
    two parallel invocations from the same operator don't stomp on
    each other's snapshot path.

    ``snap_prefix`` lets each caller carry a self-identifying path
    (``dinary-backup``, ``dinary-verify-db``, ...) so a stale
    snapshot in ``/tmp`` after a crashed invocation is traceable to
    the task that created it.

    Returns a string that ends with ``"; "`` so callers can
    concatenate their epilogue (``cat "$SNAP"`` to stream bytes home,
    a second ``sqlite3 "$SNAP" "PRAGMA ..."`` to validate the
    snapshot, or a full ``DINARY_DATA_PATH=$SNAP python -m ...``
    report invocation) without worrying about separator bookkeeping.
    """
    return (
        "set -e; "
        f"SNAP=/tmp/{snap_prefix}-$$.db; "
        'trap "rm -f \\"$SNAP\\"" EXIT; '
        f'sqlite3 "{_REMOTE_DB_PATH}" ".backup \\"$SNAP\\""; '
    )


def _remote_snapshot_cmd(module_path: str, flags: list[str]) -> str:
    """Build a remote shell command that runs a read-only report module
    against a consistent SQLite snapshot of the live DB.

    SQLite in WAL mode would let a reader open the live file
    concurrently with the running writer, but doing so races with
    in-flight checkpoints and with Litestream's replication cadence
    — the reader could land on a page mid-rewrite and surface an
    ephemeral inconsistency. Instead we ``sqlite3 .backup`` the
    primary file into ``/tmp``, point ``DINARY_DATA_PATH`` at the
    snapshot, and run the read-only report module against that
    isolated copy. The snapshot is torn down on every exit path via
    ``trap`` so a failed report never leaks a multi-hundred-MB file.

    Snapshot consistency: SQLite's online-backup API copies the
    database page-by-page while holding just enough locks to keep
    the reader's view transactionally consistent, even while the
    service keeps writing. The snapshot reflects the last
    committed transaction at the moment ``.backup`` released.

    Unique per-invocation snapshot path: ``$$`` expands to the
    remote shell's PID so two parallel ``inv report-*`` runs from
    separate laptops cannot stomp on the same ``/tmp`` file.

    ``module_path`` is the full dotted path (``dinary.reports.income``,
    ``dinary.imports.report_2d_3d``, ...) so the same wrapper serves
    both ``inv report-*`` and ``inv import-report-2d-3d --remote``.
    """
    report = f"uv run python -m {module_path}"
    if flags:
        report = f"{report} {' '.join(flags)}"
    # Prologue (``set -e`` + trap + ``sqlite3 .backup``) is shared
    # with ``inv backup`` / ``inv verify-db --remote`` / ``inv deploy``
    # pre-backup — see ``_sqlite_backup_to_tmp_snapshot_prologue``.
    return (
        _sqlite_backup_to_tmp_snapshot_prologue("dinary-report-snapshot")
        + "cd ~/dinary && source ~/.local/bin/env && "
        + f'DINARY_DATA_PATH="$SNAP" {report}'
    )


def _extract_format_flags(flags: list[str]) -> tuple[bool, bool, list[str]]:
    """Split ``flags`` into ``(as_csv, as_json, remaining)``.

    ``--csv`` / ``--json`` select the local output format and are
    consumed here. Filters (``--year``, ``--month``, ...) stay in
    ``remaining`` and travel through to the remote report module
    (they affect which rows come back). The remote always runs in
    JSON mode; ``--csv`` / ``--json`` never reach it.
    """
    as_csv = False
    as_json = False
    remaining: list[str] = []
    for flag in flags:
        if flag == "--csv":
            as_csv = True
        elif flag == "--json":
            as_json = True
        else:
            remaining.append(flag)
    return as_csv, as_json, remaining


def _extract_year_month(filter_flags: list[str]) -> tuple[int | None, tuple[int, int] | None]:
    """Pull ``--year YYYY`` / ``--month YYYY-MM`` out of filter flags.

    The values are cosmetic — they drive the expenses rich table
    title only. Row filtering has already happened on the remote
    query, so a missing / malformed value here just degrades the
    header text.
    """
    year: int | None = None
    month: tuple[int, int] | None = None
    it = iter(filter_flags)
    for token in it:
        if token == "--year":
            try:
                year = int(next(it))
            except (StopIteration, ValueError):
                year = None
        elif token == "--month":
            value = next(it, "")
            parts = value.split("-")
            if len(parts) == 2:
                try:
                    month = (int(parts[0]), int(parts[1]))
                except ValueError:
                    month = None
    return year, month
