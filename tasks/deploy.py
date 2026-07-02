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
    # A fixed sleep raced cold-start costs (migrations, Drive prefetch) and
    # falsely failed deploys that were about to come up cleanly.
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
