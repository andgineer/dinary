"""SSH transport, systemd helpers, and script builders for task modules."""

import base64
import json
import re
import shlex
import subprocess
from pathlib import Path

from .constants import (
    _REMOTE_DB_PATH,
    _UV,
    LITESTREAM_VERSION,
    LOCAL_ENV_PATH,
    LOCAL_IMPORT_SOURCES_PATH,
    REMOTE_DEPLOY_DIR,
    REMOTE_ENV_PATH,
    REMOTE_IMPORT_SOURCES_PATH,
)
from .env import host, replica_host

# ---------------------------------------------------------------------------
# Private module-level constants
# ---------------------------------------------------------------------------

# Characters that are safe to emit bare (unquoted) in a systemd EnvironmentFile
# value. Everything else gets double-quoted with internal escaping.
_ENV_SAFE_RE = re.compile(r"^[A-Za-z0-9@/.:_\-,+=]+$")

# ---------------------------------------------------------------------------
# Core SSH helpers
# ---------------------------------------------------------------------------


def ssh_run(c, cmd):
    """Run ``cmd`` on the main host (VM1) via SSH.

    The command is base64-encoded before being shipped to the remote
    shell so single-quotes / dollar signs / newlines in ``cmd`` do
    not require any further escaping by callers.
    """
    b64 = base64.b64encode(cmd.encode()).decode()
    c.run(f"ssh {host()} 'echo {b64} | base64 -d | bash'")


def ssh_replica(c, cmd):
    """Like :func:`ssh_run` but targets the replica host (VM2)."""
    b64 = base64.b64encode(cmd.encode()).decode()
    c.run(f"ssh {replica_host()} 'echo {b64} | base64 -d | bash'")


def ssh_sudo(c, cmd):
    """Run ``cmd`` on the main host with ``sudo``."""
    ssh_run(c, f"sudo {cmd}")


def ssh_capture_bytes(cmd):
    """Run ``cmd`` on the main host and return raw stdout bytes.

    Uses ``subprocess.run`` directly (not invoke) so the transport
    captures bytes before any decode — critical for UTF-8 payloads
    that span chunk boundaries.
    """
    b64 = base64.b64encode(cmd.encode()).decode()
    result = subprocess.run(
        ["ssh", host(), f"echo {b64} | base64 -d | bash"],
        capture_output=True,
        check=True,
    )
    return result.stdout


def ssh_capture(c, cmd):  # noqa: ARG001
    """Run ``cmd`` on the main host and return stdout as a string."""
    return ssh_capture_bytes(cmd).decode("utf-8")


def ssh_json(c, cmd):
    """Run ``cmd`` on the main host and parse its stdout as JSON."""
    return json.loads(ssh_capture(c, cmd))


def ssh_replica_capture_bytes(cmd):
    """Like :func:`ssh_capture_bytes` but targets the replica host (VM2)."""
    b64 = base64.b64encode(cmd.encode()).decode()
    result = subprocess.run(
        ["ssh", replica_host(), f"echo {b64} | base64 -d | bash"],
        capture_output=True,
        check=True,
    )
    return result.stdout


# ---------------------------------------------------------------------------
# Remote file writers
# ---------------------------------------------------------------------------


def write_remote_file(c, path, content):
    """Write ``content`` to ``path`` on the main host via ``sudo tee``."""
    b64 = base64.b64encode(content.encode()).decode()
    c.run(f"ssh {host()} 'echo {b64} | base64 -d | sudo tee {path} > /dev/null'")


def write_remote_replica_file(c, path, content):
    """Write ``content`` to ``path`` on the replica host via ``sudo tee``."""
    b64 = base64.b64encode(content.encode()).decode()
    c.run(
        f"ssh {replica_host()} 'echo {b64} | base64 -d | sudo tee {path} > /dev/null'",
    )


# ---------------------------------------------------------------------------
# systemd helpers
# ---------------------------------------------------------------------------


def systemd_quote(value):
    """Quote ``value`` for use in a systemd EnvironmentFile.

    Bare alphanumeric/URL-safe values pass through unquoted.
    Everything else is double-quoted with ``"``, ``$``, and ``\\``
    escaped so systemd does not expand them.
    """
    if value is None or value == "":
        return ""
    if _ENV_SAFE_RE.match(value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
    return f'"{escaped}"'


def render_service(c, name, content):
    """Upload a systemd unit file to ``/etc/systemd/system/<name>.service``."""
    path = f"/etc/systemd/system/{name}.service"
    write_remote_file(c, path, content)
    ssh_run(c, "sudo systemctl daemon-reload")


def create_service(c, name, content):
    """Upload, enable, and start a systemd service."""
    render_service(c, name, content)
    ssh_sudo(c, f"systemctl enable --now {name}")


# ---------------------------------------------------------------------------
# Env / config sync helpers
# ---------------------------------------------------------------------------


def sync_remote_env(c):
    """Upload ``.deploy/.env`` to the main host.

    Creates ``REMOTE_DEPLOY_DIR`` if absent (first-time deploy) so
    ``sudo tee`` does not fail with "No such file or directory".
    """
    content = Path(LOCAL_ENV_PATH).read_text(encoding="utf-8")
    ssh_run(c, f"mkdir -p {REMOTE_DEPLOY_DIR}")
    write_remote_file(c, REMOTE_ENV_PATH, content)


def sync_remote_import_sources(c):
    """Upload ``.deploy/import_sources.json`` if it exists locally."""
    local_path = Path(LOCAL_IMPORT_SOURCES_PATH)
    if not local_path.exists():
        return
    content = local_path.read_text(encoding="utf-8")
    ssh_run(c, f"mkdir -p {REMOTE_DEPLOY_DIR}")
    write_remote_file(c, REMOTE_IMPORT_SOURCES_PATH, content)


# ---------------------------------------------------------------------------
# Tunnel setup
# ---------------------------------------------------------------------------


def setup_tailscale(c):
    """Configure Tailscale serve for the dinary app on the main host."""
    ssh_run(
        c,
        "curl -fsSL https://tailscale.com/install.sh | sudo sh"
        " && sudo tailscale up --hostname=dinary --ssh=false",
    )
    # Allow the current user to manage serve without sudo, then enable the proxy.
    ssh_sudo(c, "tailscale set --operator=$USER")
    ssh_run(c, "tailscale serve --bg 8000")


def setup_cloudflare(c):
    """Enable and start the Cloudflare tunnel service on the main host."""
    ssh_sudo(c, "systemctl enable --now cloudflared")


# ---------------------------------------------------------------------------
# Script builders
# ---------------------------------------------------------------------------


def litestream_install_script(version=None):
    """Emit a shell script that installs Litestream on a remote Linux host.

    Idempotent: short-circuits when ``litestream`` is already on PATH.
    Supports both x86_64/amd64 and aarch64/arm64 shapes.
    """
    ver = version if version is not None else LITESTREAM_VERSION
    return (
        f"if ! command -v litestream >/dev/null; then\n"
        f"  ARCH=$(uname -m)\n"
        f"  case $ARCH in\n"
        f"    x86_64|amd64) ASSET=litestream-{ver}-linux-x86_64.deb ;;\n"
        f"    aarch64|arm64) ASSET=litestream-{ver}-linux-arm64.deb ;;\n"
        f'    *) echo "Unsupported arch $ARCH for litestream {ver}" >&2; exit 1 ;;\n'
        f"  esac\n"
        f"  URL=https://github.com/benbjohnson/litestream/releases/download/v{ver}/$ASSET\n"
        f"  curl -fsSL -o /tmp/$ASSET $URL\n"
        f"  sudo dpkg -i /tmp/$ASSET\n"
        f"  rm /tmp/$ASSET\n"
        f"fi\n"
    )


def build_setup_swap_script(*, size_gb):
    """Emit the shell script that provisions a persistent swapfile.

    Idempotent: short-circuits when ``/swapfile`` is already active.
    """
    if size_gb <= 0:
        msg = "size_gb must be a positive integer"
        raise ValueError(msg)
    return (
        "sudo bash <<'DINARY_SWAP_EOF'\n"
        "set -euo pipefail\n"
        f"if swapon --show=NAME --noheadings | grep -qx /swapfile; then\n"
        "  echo '/swapfile already active, skipping allocation'\n"
        "else\n"
        f"  fallocate -l {size_gb}G /swapfile\n"
        "  chmod 600 /swapfile\n"
        "  mkswap /swapfile\n"
        "  swapon /swapfile\n"
        "fi\n"
        'FSTAB_LINE="/swapfile none swap sw 0 0"\n'
        'grep -qxF "$FSTAB_LINE" /etc/fstab || echo "$FSTAB_LINE" >> /etc/fstab\n'
        "DINARY_SWAP_EOF"
    )


def build_ssh_tailscale_only_script():
    """Emit the shell script that rebinds sshd to Tailscale + loopback.

    Guards against Tailscale not being installed or logged out.
    Validates the new config with ``sshd -t`` before reloading.
    Rolls back the drop-in if validation fails.
    """
    return (
        "sudo bash <<'DINARY_SSH_TS_EOF'\n"
        "set -euo pipefail\n"
        "if ! command -v tailscale >/dev/null 2>&1; then\n"
        "  echo 'tailscale is not installed' >&2; exit 1\n"
        "fi\n"
        'TS_IP="$(tailscale ip -4 2>/dev/null | head -1)"\n'
        'if [ -z "$TS_IP" ]; then\n'
        "  echo 'tailscaled is not up or not logged in' >&2; exit 1\n"
        "fi\n"
        "DROPIN=/etc/ssh/sshd_config.d/10-tailscale-only.conf\n"
        'cat >"$DROPIN" <<EOC\n'
        "ListenAddress ${TS_IP}:22\n"
        "ListenAddress 127.0.0.1:22\n"
        "EOC\n"
        "if ! sshd -t 2>&1; then\n"
        '  rm -f "$DROPIN"\n'
        "  echo 'sshd -t rejected the new config; drop-in removed' >&2; exit 1\n"
        "fi\n"
        "systemctl reload ssh\n"
        "DINARY_SSH_TS_EOF"
    )


# ---------------------------------------------------------------------------
# SQLite backup prologue
# ---------------------------------------------------------------------------


def sqlite_backup_prologue(snap_prefix):
    """Return the shell snippet that takes a ``sqlite3 .backup`` snapshot.

    The snippet sets ``SNAP`` to ``/tmp/<snap_prefix>-<PID>.db``,
    registers a trap to clean it up on exit, and runs
    ``sqlite3 .backup`` against the live production DB. Callers
    append further shell commands that operate on ``$SNAP``.
    """
    return (
        f"SNAP=/tmp/{snap_prefix}-$$.db; "
        f'trap "rm -f \\"$SNAP\\"" EXIT; '
        f'sqlite3 "{_REMOTE_DB_PATH}" ".backup \\"$SNAP\\""; '
    )


# ---------------------------------------------------------------------------
# Remote snapshot command (for report / sql --remote)
# ---------------------------------------------------------------------------


def remote_snapshot_cmd(module_path, flags):
    """Build the complete shell command for a ``--remote`` report or sql run.

    Wraps ``module_path`` in a ``sqlite3 .backup`` prologue so the
    module reads from a transactionally-consistent ``/tmp`` snapshot
    rather than the live WAL-backed primary DB.
    """
    snap_name = "dinary-report-snapshot"
    prologue = sqlite_backup_prologue(snap_name)
    flag_str = (" " + shlex.join(flags)) if flags else ""
    data_env = 'DINARY_DATA_PATH="$SNAP"'
    run_cmd = f"cd ~/dinary && {data_env} {_UV} run python -m {module_path}{flag_str}"
    return f"set -e; {prologue}{run_cmd}"
