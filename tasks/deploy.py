"""Deploy and catalog tasks."""

import base64
import subprocess
import sys
from datetime import datetime as _dt
from pathlib import Path

from invoke import task

from .constants import _REMOTE_DB_PATH, DINARY_SERVICE, REMOTE_LEGACY_ENV_PATH
from .env import bind_host, host, tunnel
from .ssh_utils import (
    build_data_dir_permissions_script,
    render_service,
    sqlite_backup_prologue,
    ssh_run,
    ssh_sudo,
    sync_remote_env,
    sync_remote_import_sources,
)


@task
def deploy(c, ref="", no_start=False):
    """Deploy latest code to the server: pull, sync deps, migrate, restart.

    Options:
      --ref REF      Git tag, commit hash, or branch to deploy.
                     Without ``--ref`` the server does ``git pull`` on the
                     currently checked-out branch.
      --no-start     Deploy code but skip the final ``systemctl start``.
                     Use for DB restore flows where you need to swap the DB
                     before the service reads it.

    **Pinning a version**

    Pass ``--ref`` to deploy a specific git tag, commit hash, or branch::

        inv deploy --ref=v0.4.0
        inv deploy --ref=ee1dbf5d

    Without ``--ref`` the server does ``git pull`` on the currently checked-out
    branch.  After ``--ref`` the server is in detached HEAD; a subsequent
    ``inv deploy`` without ``--ref`` will pull on that detached commit (no-op).

    **DB restore flow (--no-start)**

    By default deploy stops the service, deploys code, and starts it back up
    (migrations run automatically on start).  Pass ``--no-start`` to skip the final start
    so you can replace the DB before the service sees it (e.g. after
    ``inv backup-cloud-restore``)::

        inv deploy --ref=v0.4.0 --no-start
        inv backup-cloud-restore
        inv restart-server

    **Catalog changes**

    Run ``inv bootstrap-catalog --yes`` separately when the hardcoded taxonomy
    changes.  Omit it on routine deploys — the seeder overwrites any manually
    edited names and sort order.

    **What the pipeline does**

    1. Pre-deploy safety backup via ``sqlite3 .backup`` (consistent snapshot).
    2. ``git pull`` (or ``git checkout <ref>``) + ``uv sync --no-dev``.
    3. Sync ``.deploy/.env`` and ``import_sources.json`` to the server.
    4. Re-render ``/etc/systemd/system/dinary.service`` (keeps ``EnvironmentFile=`` current).
    5. Build ``_static/`` with the version string.
    6. ``systemctl restart dinary`` + health check (skipped with ``--no-start``).

    Schema migrations (yoyo) run automatically when the server starts.
    """
    print("=== Pre-deploy backup ===")
    ts = _dt.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = Path(f"backups/pre-deploy-{ts}")
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
    if ref:
        ssh_run(
            c,
            f"cd ~/dinary && git fetch --tags && git checkout {ref} "
            "&& source ~/.local/bin/env && uv sync --no-dev",
        )
        print(
            f"=== WARNING: Remote is in detached HEAD at '{ref}'. "
            "Future `inv deploy` without --ref will `git pull` on whatever branch is "
            "checked out. ===",
        )
    else:
        ssh_run(c, "cd ~/dinary && git pull && source ~/.local/bin/env && uv sync --no-dev")

    print("=== Ensuring data/ directory (0700) ===")
    ssh_run(c, "mkdir -p ~/dinary/data && " + build_data_dir_permissions_script())

    print("=== Syncing .deploy/.env to server ===")
    sync_remote_env(c)

    print("=== Syncing .deploy/import_sources.json to server (if present) ===")
    sync_remote_import_sources(c)

    bh = bind_host(deploy_tunnel)
    print(f"=== Re-rendering dinary systemd unit (bind {bh}) ===")
    render_service(c, "dinary", DINARY_SERVICE.format(host=bh))

    print("=== Cleaning up legacy ~/dinary/.env (if present) ===")
    ssh_run(c, f"rm -f {REMOTE_LEGACY_ENV_PATH}")

    print("=== Building _static/ with version ===")
    ssh_run(c, "cd ~/dinary && source ~/.local/bin/env && uv run inv build-static")

    if no_start:
        print(
            "=== --no-start set: code deployed, service NOT started. ===\n"
            "=== DB restore flow:                                      ===\n"
            "===   inv backup-cloud-restore                            ===\n"
            "===   inv restart-server                                  ===\n"
            "=== Full reset flow:                                      ===\n"
            "===   ssh $HOST 'rm -f ~/dinary/data/dinary.db*'          ===\n"
            "===   inv restart-server          # creates schema on start ===\n"
            "===   inv import-catalog --yes                             ===\n"
            "===   inv import-budget-all --yes                          ===\n"
            "===   inv import-income-all --yes                          ===\n"
            "===   inv import-verify-bootstrap-all                      ===\n"
            "===   inv import-verify-income-all                         ===\n"
            "===   inv restart-server                                   ===",
        )
        return

    ssh_sudo(c, "systemctl restart dinary")
    print("=== Restarted. Checking health... ===")
    ssh_run(c, "sleep 5 && curl -s http://localhost:8000/api/health")


@task(name="bootstrap-catalog")
def bootstrap_catalog(c, yes=False):
    """Populate runtime catalog (groups/categories/tags/events) from hardcoded taxonomy.

    WARNING: overwrites any manual changes to groups, categories, tags, and
    events with the hardcoded taxonomy. Pass ``--yes`` to confirm.

    Required for every fresh deployment. The import flow (``inv import-catalog``)
    implicitly re-runs the same logic as its first step.

    Does NOT touch Google Sheets, ``import_mapping``, or ``sheet_mapping``.
    Does NOT bump ``catalog_version`` unless the taxonomy actually changed.
    """
    if not yes:
        print(
            "bootstrap-catalog overwrites any manual changes to groups, categories,\n"
            "tags, and events with the hardcoded taxonomy.\n"
            "Pass --yes to confirm.",
            file=sys.stderr,
        )
        sys.exit(1)
    ssh_run(
        c,
        "cd ~/dinary && source ~/.local/bin/env && uv run python -c '"
        "from dinary.services.seed_config import bootstrap_catalog; "
        "import json; print(json.dumps(bootstrap_catalog()))'",
    )


@task(name="import-config")
def import_config(c):
    """Seed the catalog from the configured source sheets (non-destructive).

    Requires ``.deploy/import_sources.json`` to exist locally AND on
    the server (uploaded via ``sync_remote_import_sources`` during
    ``inv deploy`` / ``inv setup-server``). Fails loud with a pointer to the
    repo-root ``imports/`` directory when the file is missing or empty.
    """
    ssh_run(
        c,
        "cd ~/dinary && source ~/.local/bin/env && uv run python -c '"
        "from dinary.imports.seed import seed_from_sheet; "
        "import json; print(json.dumps(seed_from_sheet()))'",
    )
