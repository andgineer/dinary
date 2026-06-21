"""Deploy and catalog tasks."""

import base64
import subprocess
import sys
from datetime import datetime as _dt
from pathlib import Path

from invoke import task

from tasks.devtools.constants import _REMOTE_DB_PATH, DINARY_SERVICE, REMOTE_DEPLOY_DIR
from tasks.devtools.env import bind_host, host, tunnel
from tasks.ssh_utils import (
    build_data_dir_permissions_script,
    render_service,
    sqlite_backup_prologue,
    ssh_capture,
    ssh_run,
    ssh_sudo,
    sync_remote_env,
    sync_remote_file,
)

_DEPLOY_FILES = [
    (".deploy/llms.toml", f"{REMOTE_DEPLOY_DIR}/llms.toml"),
]

_LEGACY_REMOTE_FILES = [
    f"{REMOTE_DEPLOY_DIR}/llm_providers.toml",
]


def sync_remote_deploy_files(c) -> None:
    """Sync .deploy/ config and secrets files to the server."""
    for path in _LEGACY_REMOTE_FILES:
        ssh_run(c, f"rm -f {path}")
    for local, remote in _DEPLOY_FILES:
        sync_remote_file(c, local, remote)


def _target_migration_head(ref: str) -> str | None:
    """Return the last migration ID present in ``ref`` via git ls-tree."""
    result = subprocess.run(
        ["git", "ls-tree", "--name-only", ref, "src/dinary/db/migrations/"],
        capture_output=True,
        text=True,
        check=True,
    )
    stems = sorted(
        Path(line).stem
        for line in result.stdout.splitlines()
        if line.endswith(".sql") and not line.endswith(".rollback.sql")
    )
    return stems[-1] if stems else None


def _migrations_to_rollback(applied: list[str], target_head: str) -> list[str]:
    """Return applied migration IDs that sort after target_head."""
    return sorted(m for m in applied if m > target_head)


def _server_applied_migrations(c) -> list[str]:
    """Return migration IDs currently tracked in the server's _yoyo_migration table."""
    raw = ssh_capture(
        c,
        f"sqlite3 {_REMOTE_DB_PATH}"  # noqa: S608
        ' "SELECT id FROM _yoyo_migration ORDER BY id" 2>/dev/null || true',
    )
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _downgrade_if_needed(c, ref: str, backup_dir: Path) -> None:
    """Detect schema downgrade requirement and run yoyo rollback before checkout.

    Must be called while the server still has its *current* code checked out so
    the .rollback.sql files for any extra migrations are still on disk.
    """
    target_head = _target_migration_head(ref)
    if target_head is None:
        return

    applied = _server_applied_migrations(c)
    to_roll_back = _migrations_to_rollback(applied, target_head)
    if not to_roll_back:
        return

    server_head = applied[-1] if applied else "(none)"
    print(
        f"\n=== WARNING: DB DOWNGRADE REQUIRED ===\n"
        f"Server applied migrations up to:     {server_head}\n"
        f"Target version has migrations up to: {target_head}\n"
        f"\nMigrations to be rolled back: {', '.join(to_roll_back)}\n"
        f"This will PERMANENTLY DESTROY any data in columns/tables\n"
        f"added by those migrations.\n"
        f"\nPre-deploy backup saved to: {backup_dir / 'dinary.db'}\n",
    )
    answer = input('Type "yes" to proceed with rollback, or Ctrl-C to abort: ').strip()
    if answer != "yes":
        print("Aborted.", file=sys.stderr)
        sys.exit(1)

    first_extra = to_roll_back[0]
    print(f"=== Rolling back to {target_head} ===")
    ssh_run(
        c,
        f"cd ~/dinary && source ~/.local/bin/env && "
        f"uv run yoyo rollback --batch -r {first_extra} "
        f"--database 'sqlite:///{_REMOTE_DB_PATH}' "
        f"src/dinary/db/migrations",
    )


@task
def deploy(c, ref="", no_start=False):
    """Deploy a specific version to the server.

    --ref is required (tag, commit, or branch)::

        inv deploy --ref=main
        inv deploy --ref=v0.4.0
        inv deploy --ref=ee1dbf5d

    Pipeline: backup → downgrade DB if needed (with confirmation) →
    git checkout → uv sync → restart → health check.

    Pass --no-start to skip restart (use before inv restore-yadisk).
    See https://andgineer.github.io/dinary/operations for deploy+restore runbooks.
    """
    if not ref:
        print("--ref is required: specify a git tag, commit hash, or branch.", file=sys.stderr)
        sys.exit(1)
    print("=== Pre-deploy backup ===")
    ts = _dt.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = Path(f"data/backups/pre-deploy-{ts}")
    backup_dir.mkdir(parents=True, exist_ok=True)
    deploy_host = host()
    deploy_tunnel = tunnel()
    remote_cmd = (
        "set -e; "
        f'if [ ! -f "{_REMOTE_DB_PATH}" ]; then '
        "  echo __SKIP_NO_DB__ 1>&2; exit 0; "
        "fi; " + sqlite_backup_prologue("dinary-pre-deploy-backup") + 'cat "$SNAP"'
    )
    b64 = base64.b64encode(remote_cmd.encode()).decode()
    local_db = backup_dir / "dinary.db"
    need_cleanup = False
    with local_db.open("wb") as fh:
        try:
            subprocess.run(
                ["ssh", deploy_host, f"echo {b64} | base64 -d | bash"],
                stdout=fh,
                check=True,
            )
        except subprocess.CalledProcessError:
            print(
                "=== Pre-deploy backup failed; continuing with deploy. ===",
            )
            need_cleanup = True
    if not need_cleanup and local_db.exists() and local_db.stat().st_size == 0:
        print(
            f"=== Pre-deploy backup skipped (no {_REMOTE_DB_PATH} yet); "
            "continuing with deploy. ===",
        )
        need_cleanup = True
    if need_cleanup:
        local_db.unlink(missing_ok=True)

    if not need_cleanup:
        _downgrade_if_needed(c, ref, backup_dir)

    print("=== Deploying dinary ===")
    ssh_run(
        c,
        f"cd ~/dinary && git fetch --tags && git checkout {ref} "
        "&& source ~/.local/bin/env && uv sync --no-dev --no-group analytics",
    )

    print("=== Ensuring data/ directory (0700) ===")
    ssh_run(c, "mkdir -p ~/dinary/data && " + build_data_dir_permissions_script())

    print("=== Syncing .deploy/.env to server ===")
    sync_remote_env(c)
    sync_remote_deploy_files(c)

    bh = bind_host(deploy_tunnel)
    print(f"=== Re-rendering dinary systemd unit (bind {bh}) ===")
    render_service(c, "dinary", DINARY_SERVICE.format(host=bh))

    print("=== Building _static/ with version ===")
    ssh_run(c, "cd ~/dinary && source ~/.local/bin/env && uv run inv build-static")

    if no_start:
        print(
            "=== --no-start set: code deployed, service NOT started. ===\n"
            "=== DB restore flow:                                      ===\n"
            "===   inv restore-yadisk                                  ===\n"
            "===   inv restart-server                                  ===\n"
            "=== Full reset flow:                                      ===\n"
            "===   ssh $HOST 'rm -f ~/dinary/data/dinary.db*'          ===\n"
            "===   inv restart-server          # creates schema + categories ===",
        )
        return

    ssh_sudo(c, "systemctl restart dinary")
    print("=== Restarted. Waiting for /api/health (up to 30s) ... ===")
    # Cold starts can exceed a fixed ``sleep 5`` whenever yoyo runs a
    # migration on boot or ``sheet_mapping`` prefetch hits a slow
    # Drive round-trip; the old ``sleep 5 && curl`` raced those costs
    # and surfaced as a deploy failure (curl exit 7) even though the
    # service was about to come up cleanly. Probe once per second
    # for up to 30s and only fail when the service is genuinely down.
    health_check = (
        "for i in $(seq 1 30); do "
        "  if out=$(curl -fsS http://localhost:8000/api/health 2>&1); then "
        '    echo "$out"; exit 0; '
        "  fi; "
        "  sleep 1; "
        "done; "
        'echo "health-check failed after 30s; last error: $out" >&2; '
        "exit 1"
    )
    ssh_run(c, health_check)
