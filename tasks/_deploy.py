"""Deploy, service control, and migration tasks."""

import base64
import subprocess
from datetime import datetime as _dt
from pathlib import Path

from invoke import task

from ._common import (
    _REMOTE_DB_PATH,
    DINARY_SERVICE,
    REMOTE_LEGACY_ENV_PATH,
    _bind_host,
    _host,
    _render_service,
    _replica_host,
    _sqlite_backup_to_tmp_snapshot_prologue,
    _ssh,
    _ssh_sudo,
    _sync_remote_env,
    _sync_remote_import_sources,
    _tunnel,
)


@task
def deploy(c, ref="", no_restart=False):
    """Deploy latest code: git pull, sync deps, render version, restart service.

    Use --ref to deploy a specific version. Use --no-restart for the coordinated
    reset flow: it skips both the post-deploy systemctl restart and the
    auto-applied schema migration, because the very next step in that flow is
    `inv stop` followed by `rm -f ~/dinary/data/dinary.db*` +
    `inv migrate` + `inv import-catalog --yes` which rebuilds the single DB
    anyway.

    Run ``inv bootstrap-catalog`` separately when taxonomy changes. Omit it
    on routine deploys — the seeder overwrites manually edited names and sort order.

    Pipeline (ordering matters — see ``.plans/architecture.md`` and the
    original inline-fk-drop-and-reset-db plan for the constraints):

    * Pre-deploy safety-net backup uses ``sqlite3 .backup`` on the
      server (same mechanism as ``inv backup``) to take a
      transactionally consistent snapshot of ``dinary.db``. A raw
      ``scp -r data/`` of the live WAL triple
      (``.db`` + ``-wal`` + ``-shm``) would not produce a consistent
      image because in-flight WAL frames could be mid-write while
      the files are being copied.
    * ``_sync_remote_env`` writes to
      ``~/dinary/.deploy/.env`` via ``sudo tee``, which does
      not ``mkdir -p`` — ``_sync_remote_env`` itself now runs
      ``mkdir -p`` first.
    * We re-render ``/etc/systemd/system/dinary.service`` every
      deploy so ``EnvironmentFile=`` always matches the canonical
      ``.deploy/.env`` path. ``git pull`` never touches the unit
      file, so without this the old unit (pointing at the legacy
      top-level ``.env``) would survive the deploy and the
      post-deploy ``systemctl restart`` would start dinary with an
      unresolved ``EnvironmentFile``.
    * Legacy ``~/dinary/.env`` is removed *after* the unit
      has been rewritten so systemd is never left with a dangling
      ``EnvironmentFile`` pointer.
    """
    print("=== Pre-deploy backup ===")
    ts = _dt.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = Path(f"backups/pre-deploy-{ts}")
    backup_dir.mkdir(parents=True, exist_ok=True)
    host = _host()
    tunnel = _tunnel()
    remote_cmd = (
        "set -e; "
        f'if [ ! -f "{_REMOTE_DB_PATH}" ]; then '
        "  echo __SKIP_NO_DB__ 1>&2; exit 0; "
        "fi; " + _sqlite_backup_to_tmp_snapshot_prologue("dinary-pre-deploy-backup") + 'cat "$SNAP"'
    )
    b64 = base64.b64encode(remote_cmd.encode()).decode()
    local_db = backup_dir / "dinary.db"
    need_cleanup = False
    with local_db.open("wb") as fh:
        try:
            subprocess.run(
                ["ssh", host, f"echo {b64} | base64 -d | bash"],
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
        _ssh(
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
        _ssh(c, "cd ~/dinary && git pull && source ~/.local/bin/env && uv sync --no-dev")

    print("=== Ensuring data/ directory ===")
    _ssh(c, "mkdir -p ~/dinary/data")

    print("=== Syncing .deploy/.env to server ===")
    _sync_remote_env(c)

    print("=== Syncing .deploy/import_sources.json to server (if present) ===")
    _sync_remote_import_sources(c)

    bind_host = _bind_host(tunnel)
    print(f"=== Re-rendering dinary systemd unit (bind {bind_host}) ===")
    _render_service(c, "dinary", DINARY_SERVICE.format(host=bind_host))

    print("=== Cleaning up legacy ~/dinary/.env (if present) ===")
    _ssh(c, f"rm -f {REMOTE_LEGACY_ENV_PATH}")

    if not no_restart:
        print("=== Applying schema migrations ===")
        _ssh(
            c,
            "cd ~/dinary && source ~/.local/bin/env && "
            "uv run python -c 'from dinary.services import ledger_repo; ledger_repo.init_db(); "
            'print("Migrated data/dinary.db")\'',
        )
    print("=== Building _static/ with version ===")
    _ssh(c, "cd ~/dinary && source ~/.local/bin/env && uv run inv build-static")

    if no_restart:
        print(
            "=== --no-restart set: SKIPPING systemctl restart and schema migration. ===\n"
            "=== Next steps in the coordinated reset flow:   ===\n"
            "===   inv stop                                  ===\n"
            "===   ssh $HOST 'rm -f ~/dinary/data/dinary.db*'  ===\n"
            "===   inv migrate                               ===\n"
            "===   inv import-catalog --yes                  ===\n"
            "===   inv import-budget-all --yes               ===\n"
            "===   inv import-income-all --yes               ===\n"
            "===   inv verify-bootstrap-import-all           ===\n"
            "===   inv verify-income-equivalence-all         ===\n"
            "===   inv start                                 ===",
        )
        return

    _ssh_sudo(c, "systemctl restart dinary")
    print("=== Restarted. Checking health... ===")
    _ssh(c, "sleep 5 && curl -s http://localhost:8000/api/health")


@task
def stop(c):
    """Stop the dinary systemd service (used during the coordinated reset flow)."""
    _ssh_sudo(c, "systemctl stop dinary")
    print("=== dinary stopped ===")
    _ssh_sudo(c, "systemctl is-active dinary || true")


@task
def start(c):
    """Start the dinary systemd service after a coordinated reset completes."""
    _ssh_sudo(c, "systemctl start dinary")
    print("=== dinary started, checking health... ===")
    _ssh(c, "sleep 5 && curl -s http://localhost:8000/api/health")


@task
def logs(c, follow=False, lines=100):
    """Show server logs. Use -f to follow, -l N for line count."""
    flag = "-f" if follow else f"-n {lines} --no-pager"
    c.run(f"ssh {_host()} 'sudo journalctl -u dinary {flag}'")


@task
def status(c):
    """Show server status."""
    tunnel = _tunnel()
    _ssh_sudo(c, "systemctl status dinary --no-pager")
    if tunnel == "tailscale":
        _ssh(c, "tailscale serve status")
    elif tunnel == "cloudflare":
        _ssh_sudo(c, "systemctl status cloudflared --no-pager")


@task
def ssh(c):
    """Open SSH session to the server."""
    c.run(f"ssh {_host()}", pty=True)


@task(name="ssh-replica")
def ssh_replica(c):
    """Open SSH session to the replica (VM2)."""
    c.run(f"ssh {_replica_host()}", pty=True)


@task(name="bootstrap-catalog")
def bootstrap_catalog(c):
    """Populate runtime catalog (groups/categories/tags/events) from hardcoded taxonomy.

    Non-destructive and idempotent. Required for every deployment —
    non-import users rely on this as their only catalog-population
    path, and the import flow (``inv import-catalog``) implicitly
    re-runs the same logic as its first step.

    Does NOT touch Google Sheets, ``import_mapping``, or
    ``sheet_mapping``. Does NOT bump ``catalog_version`` unless the
    hardcoded taxonomy actually changed from what's already on disk.
    """
    _ssh(
        c,
        "cd ~/dinary && source ~/.local/bin/env && uv run python -c '"
        "from dinary.services.seed_config import bootstrap_catalog; "
        "import json; print(json.dumps(bootstrap_catalog()))'",
    )


@task(name="import-config")
def import_config(c):
    """Seed the catalog from the configured source sheets (non-destructive).

    Requires ``.deploy/import_sources.json`` to exist locally AND on
    the server (uploaded via ``_sync_remote_import_sources`` during
    ``inv deploy`` / ``inv setup``). Fails loud with a pointer to the
    repo-root ``imports/`` directory when the file is missing or empty.
    """
    _ssh(
        c,
        "cd ~/dinary && source ~/.local/bin/env && uv run python -c '"
        "from dinary.imports.seed import seed_from_sheet; "
        "import json; print(json.dumps(seed_from_sheet()))'",
    )


@task(name="migrate")
def migrate(c):
    """Apply pending migrations to ``data/dinary.db`` on the server."""
    _ssh(
        c,
        "cd ~/dinary && source ~/.local/bin/env && "
        "uv run python -c 'from dinary.services import ledger_repo; "
        'ledger_repo.init_db(); print("Migrated data/dinary.db")\'',
    )
