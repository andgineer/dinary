"""Backup, replica, and Litestream tasks."""

import base64
import getpass
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from invoke import task

from dinary.tools.backup_snapshots import (
    BACKUP_RCLONE_PATH,
    BACKUP_RCLONE_REMOTE,
    BACKUP_STALE_HOURS,
    assert_local_binaries,
    check_backup_freshness,
    format_backup_status_line,
    parse_snapshot_lsjson,
    parse_snapshot_timestamp,  # noqa: F401
    pick_snapshot,
    print_snapshot_list,
    sqlite_row_count,
)

from .constants import (
    LITESTREAM_SERVICE,
    LOCAL_LITESTREAM_CONFIG_PATH,
    LOCAL_LITESTREAM_EXAMPLE_PATH,
    REMOTE_LITESTREAM_CONFIG_PATH,
    REPLICA_LITESTREAM_DIR,
)
from .env import _env, replica_host
from .ssh_utils import (
    build_setup_swap_script,
    build_ssh_tailscale_only_script,
    create_service,
    litestream_install_script,
    ssh_replica,
    ssh_replica_capture_bytes,
    ssh_run,
    ssh_sudo,
    write_remote_file,
)

# ---------------------------------------------------------------------------
# Yandex rclone helpers
# ---------------------------------------------------------------------------


def replica_has_working_yandex_remote():
    """Return True iff VM2's rclone has a ``yandex:`` remote that
    actually works (auth + URL + scope + network all green).

    The contract here is "do we need to prompt the operator?", not
    "does a config section exist?". A half-written config (missing
    ``url``, wrong app-password, revoked scope) still needs the
    interactive bootstrap — otherwise the retention timer would
    quietly fail every night against a broken remote. So the probe:

    1. ``rclone listremotes`` must include exact line ``yandex:``
       — substring match against e.g. ``yandex-old:`` would falsely
       pass.
    2. ``rclone lsd yandex:`` must succeed. This smoke-tests the
       entire stack: URL scheme, credentials, scope, Yandex
       reachability.

    If the remote exists but fails the smoke-test, we delete it
    inline before returning False. That way the next
    :func:`_ensure_yandex_rclone_configured` call prompts fresh
    credentials instead of silently keeping a broken config around
    across runs (the exact bug that bit us when
    ``rclone config create`` silently dropped ``key=value`` fields).
    """
    probe = (
        "set -eu\n"
        "if ! rclone listremotes 2>/dev/null | grep -qx 'yandex:'; then\n"
        "  exit 1\n"
        "fi\n"
        "if ! rclone lsd yandex: >/dev/null 2>&1; then\n"
        "  rclone config delete yandex >/dev/null 2>&1 || true\n"
        "  echo 'stale yandex: remote removed on VM2 — re-prompting' >&2\n"
        "  exit 1\n"
        "fi\n"
    )
    result = subprocess.run(
        ["ssh", replica_host(), probe],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.stderr:
        sys.stderr.write(result.stderr)
    return result.returncode == 0


def prompt_yandex_credentials():
    """Interactive prompt for Yandex login + app-password.

    Split out so the IO layer is mockable — the in-flow tests stub
    this to feed deterministic credentials without poking a real
    terminal. Returns ``(login, app_password)``; raises SystemExit
    on empty input to keep the caller out of the partial-config
    weeds (a half-created rclone remote is worse than no remote).
    """
    print(
        "\n"
        "=== Yandex.Disk WebDAV credentials ===\n"
        "\n"
        "IMPORTANT: Yandex WebDAV does NOT accept your regular Yandex\n"
        "account password. You need an APP-PASSWORD — a separate,\n"
        "scoped password Yandex generates for one specific app.\n"
        "\n"
        "How to get one:\n"
        "  1. Open https://id.yandex.ru/security/app-passwords\n"
        '  2. Click "Create a new password".\n'
        '  3. Pick the "Files" (Файлы / WebDAV) category — NOT Mail,\n'
        "     Calendar, or generic.\n"
        "  4. Yandex shows a 16-character password ONCE (often rendered\n"
        "     as 4 blocks of 4 letters). Copy it immediately; it\n"
        "     cannot be retrieved later, only regenerated.\n"
        "  5. Paste it below. You can revoke it from the same page at\n"
        "     any time without affecting your main Yandex password.\n"
        "\n"
        "Plaintext never touches disk. The obscured form is written\n"
        "to ~ubuntu/.config/rclone/rclone.conf on VM2.\n",
    )
    login = input("Yandex login (without @yandex.ru): ").strip()
    if not login:
        sys.stderr.write("Empty login; aborting.\n")
        sys.exit(1)
    app_password = getpass.getpass(
        "Yandex APP-PASSWORD (generate at https://id.yandex.ru/security/app-passwords → Files): ",
    )
    if not app_password:
        sys.stderr.write("Empty app-password; aborting.\n")
        sys.exit(1)
    return login, app_password


def install_yandex_rclone_remote(login: str, app_password: str) -> None:
    """Create the ``yandex:`` WebDAV remote on VM2 without shell leakage.

    Security shape:

    * Login AND app-password travel as two lines on the ssh stdin,
      consumed by ``read -r`` / ``read -rs``. Neither lands in argv
      (``ps`` listing), shell history, or the script image shipped
      to VM2. The plaintext password additionally dies inside
      ``rclone obscure -`` the moment it is converted to its
      obscured on-disk form.
    * ``rclone config create`` writes the obscured form to
      ``~ubuntu/.config/rclone/rclone.conf``; that is the same format
      the ``rclone config`` interactive wizard produces, so subsequent
      manual edits remain possible.

    Two sharp invariants past versions of this helper got wrong:

    * ``rclone config create`` wants ``key value`` pairs separated by
      spaces, NOT ``key=value``. The latter form silently drops
      fields on current rclone releases: an earlier version of this
      helper produced a ``[yandex]`` section with no ``url`` line,
      which then failed every operation with
      ``unsupported protocol scheme ""`` — and stuck around across
      re-runs because ``rclone listremotes`` still reported
      ``yandex:``.
    * ``rclone config create`` re-obscures ``pass`` by default, so
      feeding the already-obscured value without ``--no-obscure``
      corrupts it. We pass ``--no-obscure`` explicitly.

    Smoke-tests end-to-end with ``rclone lsd yandex:``. If the smoke
    test fails (wrong app-password, wrong scope, typo, Yandex
    unreachable) we **roll back** by deleting the just-written
    ``yandex`` remote, so the next ``inv setup-replica``
    re-prompts for fresh credentials instead of silently reusing the
    broken config.
    """
    inner = (
        "set -euo pipefail\n"
        "read -r LOGIN\n"
        "read -rs PASS\n"
        'OBS=$(printf "%s" "$PASS" | rclone obscure -)\n'
        "rclone config create --no-obscure yandex webdav "
        "url https://webdav.yandex.ru "
        "vendor other "
        'user "$LOGIN" '
        'pass "$OBS" >/dev/null\n'
        "echo '--- written [yandex] config (pass redacted) ---' >&2\n"
        "sed -n '/^\\[yandex\\]/,/^\\[/p' "
        '"$HOME/.config/rclone/rclone.conf" '
        "| sed 's/^pass = .*/pass = <redacted>/' >&2\n"
        "echo '--- smoke test: rclone lsd yandex: ---' >&2\n"
        "if ! rclone lsd yandex: --low-level-retries 1 --retries 1 -v; then\n"
        "  rclone config delete yandex >/dev/null 2>&1 || true\n"
        "  echo '' >&2\n"
        "  echo 'rclone lsd yandex: failed; broken remote removed from VM2' >&2\n"
        "  exit 1\n"
        "fi\n"
    )
    inner_b64 = base64.b64encode(inner.encode()).decode()
    outer = (
        "set -euo pipefail; "
        "T=$(mktemp); "
        'trap "rm -f \\"$T\\"" EXIT; '
        f'echo {inner_b64} | base64 -d > "$T"; '
        'bash "$T"'
    )
    proc = subprocess.run(
        ["ssh", replica_host(), outer],
        input=f"{login}\n{app_password}\n",
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        sys.stderr.write(
            "\nrclone config failed on the replica. Common causes:\n"
            "  * a regular Yandex account password was used instead of\n"
            "    an APP-PASSWORD. Yandex WebDAV accepts only app-\n"
            "    passwords from https://id.yandex.ru/security/app-passwords\n"
            "    (even on accounts without 2FA).\n"
            "  * the app-password was created for a category other than\n"
            '    "Files" (Файлы / WebDAV) — e.g. "Mail" tokens are\n'
            "    scoped differently and the WebDAV endpoint rejects them.\n"
            '  * the login was a display name or email (e.g. "Joe Doe",\n'
            '    "joe@yandex.ru") instead of the bare Yandex login.\n'
            "  * the app-password was mistyped (it is usually shown as\n"
            "    4 blocks of 4 letters; paste WITHOUT spaces).\n"
            "  * the replica cannot reach https://webdav.yandex.ru.\n"
            "\n"
            "The partially-created 'yandex:' remote has been rolled\n"
            "back on VM2.\n"
            "Re-run `inv setup-replica` to try again; it will\n"
            "prompt for credentials fresh.\n",
        )
        sys.exit(proc.returncode)


def ensure_yandex_rclone_configured(c):  # noqa: ARG001
    """Interactive bootstrap for the ``yandex:`` rclone remote on VM2.

    No-op when the remote already exists — the typical flow on
    re-runs of :func:`setup_replica_backup`. The first-time path
    prompts the operator for their Yandex login + app-password (app-
    password URL printed in the prompt text) and writes the remote
    to VM2 non-interactively via ``rclone config create``.

    The interactive OAuth flow that the rclone wizard would trigger
    is avoided on purpose: VM2 is headless, and the
    laptop-authorize/copy-token dance across machines is an error
    magnet during a future disaster recovery. WebDAV + an app-
    password is equivalent for our access pattern (PUT/DELETE of
    uploaded files) and the app-password can be revoked from
    Yandex's account UI at any time.
    """
    if replica_has_working_yandex_remote():
        print("yandex: remote already configured and healthy — skipping prompt.")
        return
    login, app_password = prompt_yandex_credentials()
    install_yandex_rclone_remote(login, app_password)
    print("yandex: remote configured and verified (rclone lsd succeeded).")


# ---------------------------------------------------------------------------
# Script / unit builders
# ---------------------------------------------------------------------------


def _build_setup_replica_packages_script() -> str:
    """Emit the apt step of the replica bootstrap.

    Kept as a pure helper so tests can pin the observable contract
    (non-interactive apt, explicit ``unattended-upgrades`` install)
    without mocking SSH.

    ``DEBIAN_FRONTEND=noninteractive`` is required: without it
    ``apt-get install`` on a fresh Ubuntu cloud image can block on a
    postfix / grub debconf prompt that the replica never answers,
    turning the bootstrap into a silent hang.
    """
    return (
        "sudo bash <<'DINARY_REPLICA_PKG_EOF'\n"
        "set -euo pipefail\n"
        "export DEBIAN_FRONTEND=noninteractive\n"
        "apt-get update -qq\n"
        "apt-get install -y -qq unattended-upgrades\n"
        "DINARY_REPLICA_PKG_EOF\n"
    )


def _build_setup_replica_litestream_dir_script() -> str:
    """Emit the ``/var/lib/litestream`` provisioning step.

    Pinned via :data:`REPLICA_LITESTREAM_DIR` so the ``inv
    litestream-setup`` replica URL on VM1 and the directory created
    here on VM2 cannot drift apart silently.

    Mode ``0750`` with ``ubuntu:ubuntu`` ownership lets the SFTP
    receiver (logged in as ``ubuntu``) write WAL segments without
    granting world-read to the replica stream, which contains full
    row data pre-compaction.
    """
    return (
        "sudo bash <<'DINARY_REPLICA_DIR_EOF'\n"
        "set -euo pipefail\n"
        f"mkdir -p {REPLICA_LITESTREAM_DIR}\n"
        f"chown ubuntu:ubuntu {REPLICA_LITESTREAM_DIR}\n"
        f"chmod 750 {REPLICA_LITESTREAM_DIR}\n"
        f"ls -ld {REPLICA_LITESTREAM_DIR}\n"
        "DINARY_REPLICA_DIR_EOF\n"
    )


# ---------------------------------------------------------------------------
# Snapshot inventory helpers
# ---------------------------------------------------------------------------


def yadisk_list_snapshots():
    """Return ``[(filename, size_bytes), ...]`` of backups on Yandex.Disk.

    Uses ``rclone lsjson`` against the operator-local ``yandex:``
    remote (the one configured on the machine running
    :func:`restore_from_yadisk`). Shape/sort contract is inherited
    from :func:`_parse_snapshot_lsjson`.
    """
    raw = subprocess.check_output(
        ["rclone", "lsjson", f"{BACKUP_RCLONE_REMOTE}:{BACKUP_RCLONE_PATH}/", "--files-only"],
        text=True,
    )
    return parse_snapshot_lsjson(raw)


def replica_list_snapshots():
    """Like :func:`_yadisk_list_snapshots` but asks VM2 over SSH.

    Used by :func:`backup_status` so the monitoring path reuses the
    already-configured ``yandex:`` remote on VM2. The laptop can
    then run freshness checks from cron without keeping its own
    Yandex WebDAV credentials.
    """
    raw = ssh_replica_capture_bytes(
        f"rclone lsjson {BACKUP_RCLONE_REMOTE}:{BACKUP_RCLONE_PATH}/ --files-only",
    ).decode("utf-8")
    return parse_snapshot_lsjson(raw)


def _prompt_restore_confirmation(target_db, picked):
    """Interactive "type yes" gate before overwriting a non-empty DB.

    Shows row count + size + mtime of the file that will be replaced
    and the size of the incoming snapshot so the operator can sanity-
    check they are about to lose ~nothing (debug DB case) or a lot
    (prod case). Any input other than the literal ``yes`` aborts.

    Why not a simple y/n: ``y`` is a one-keypress accept and every
    heavy-destructive CLI tool I want to keep safe asks for a full
    word precisely so ``Enter`` cannot accidentally commit.
    """
    row_count = sqlite_row_count(target_db)
    size_kb = target_db.stat().st_size / 1024
    mtime = datetime.fromtimestamp(target_db.stat().st_mtime, tz=UTC).strftime(
        "%Y-%m-%d %H:%M UTC",
    )
    print(
        f"About to overwrite {target_db} ({row_count:,} expense rows, "
        f"{size_kb:,.1f} KB, mtime {mtime})\n"
        f"with snapshot {picked[0]} ({picked[1] / 1024:,.1f} KB compressed).\n"
        f"The previous file will be saved as "
        f"{target_db.name}.before-restore-<UTC-ISO>.\n"
        f"WARNING: stop the server before proceeding to avoid WAL corruption.",
    )
    answer = input("Type 'yes' to proceed: ").strip()
    if answer != "yes":
        sys.stderr.write("Aborted.\n")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


@task(name="setup-replica")
def setup_replica(c, swap_size_gb=1, no_swap=False, no_tailscale=False):
    """Bootstrap VM2 (Litestream SFTP replica) and configure VM1 to replicate to it.

    VM2 is intentionally minimal: no Python app, no dinary service. Its only
    job is to accept WAL segments over SFTP from VM1's ``litestream.service``.

    Preconditions:

    * ``DINARY_REPLICA_HOST`` is set in ``.deploy/.env``.
    * Tailscale is installed and logged in on VM2 (``tailscale up`` requires a
      human-approved browser click — not automated here).
    * A second terminal with an open SSH session to VM2 is recommended before
      running — the final step closes public TCP/22.
    * ``.deploy/litestream.yml`` exists locally (copy from
      ``.deploy.example/litestream.yml`` and fill in the SFTP target).

    Idempotent: all steps short-circuit on re-apply.

    Flags:
        --swap-size-gb N   Swap size in gigabytes (default 1).
        --no-swap          Skip swap provisioning.
        --no-tailscale     Skip restricting sshd to Tailscale + loopback on VM2.
    """
    size = int(swap_size_gb)
    print(f"=== Installing baseline packages on {replica_host()} ===")
    ssh_replica(c, _build_setup_replica_packages_script())
    print(f"=== Provisioning {REPLICA_LITESTREAM_DIR} ===")
    ssh_replica(c, _build_setup_replica_litestream_dir_script())
    if not no_swap:
        print("=== Allocating swap on VM2 ===")
        ssh_replica(c, build_setup_swap_script(size_gb=size))
    if not no_tailscale:
        print("=== Restricting sshd to Tailscale + loopback on VM2 ===")
        ssh_replica(c, build_ssh_tailscale_only_script())

    print("=== Configuring VM1 Litestream replicator ===")
    if not Path(LOCAL_LITESTREAM_CONFIG_PATH).exists():
        print(
            f"No {LOCAL_LITESTREAM_CONFIG_PATH} locally.\n"
            f"Copy {LOCAL_LITESTREAM_EXAMPLE_PATH} to {LOCAL_LITESTREAM_CONFIG_PATH} "
            "and fill in the SFTP target, then re-run.",
            file=sys.stderr,
        )
        sys.exit(1)
    ssh_run(c, litestream_install_script())
    content = Path(LOCAL_LITESTREAM_CONFIG_PATH).read_text(encoding="utf-8")
    write_remote_file(c, REMOTE_LITESTREAM_CONFIG_PATH, content)
    ssh_sudo(
        c,
        f"bash -c 'chown root:root {REMOTE_LITESTREAM_CONFIG_PATH} "
        f"&& chmod 644 {REMOTE_LITESTREAM_CONFIG_PATH}'",
    )
    create_service(c, "litestream", LITESTREAM_SERVICE)
    ssh_run(
        c,
        "sleep 3 && systemctl is-active litestream && "
        "sudo journalctl -u litestream -n 20 --no-pager",
    )

    print("=== Replica bootstrap done ===")


@task(name="replica-resync")
def replica_resync(c):
    """Resync the Litestream replica after a DB restore.

    After restoring ``data/dinary.db`` from a Yadisk snapshot the
    replica's WAL position no longer matches the primary.  This task
    stops litestream on VM2, removes its stale DB copy so litestream
    will pull a fresh restore from Yadisk on next start, then restarts
    the service and waits for it to become active.

    Run automatically by ``inv backup-yadisk-restore`` unless
    ``--no-resync`` is passed.
    """
    rhost = replica_host()
    print(f"=== Stopping litestream on {rhost} ===")
    ssh_replica(c, "sudo systemctl stop litestream")
    print("=== Removing stale replica DB ===")
    ssh_replica(c, f"rm -f {REPLICA_LITESTREAM_DIR}/dinary.db*")
    print("=== Starting litestream (will restore from Yadisk) ===")
    ssh_replica(c, "sudo systemctl start litestream")
    ssh_replica(
        c,
        "sleep 5 && systemctl is-active litestream && "
        "sudo journalctl -u litestream -n 20 --no-pager",
    )
    print("=== Replica resync done ===")


@task(name="backup-cloud-status")
def backup_status(_c, max_age_hours=None, json_output=False):
    """Check freshness of the newest Yandex.Disk backup.

    Prints a one-line summary and exits 0 when the newest snapshot
    is within ``--max-age-hours`` (default :data:`BACKUP_STALE_HOURS`),
    non-zero otherwise.

    Flags:
        --max-age-hours N   Freshness threshold in hours.
        --json-output       Emit a JSON object instead of the human summary.
    """
    threshold = float(max_age_hours) if max_age_hours is not None else float(BACKUP_STALE_HOURS)
    snapshots = replica_list_snapshots()
    now = datetime.now(tz=UTC)
    verdict = check_backup_freshness(snapshots, now, threshold)
    if json_output:
        print(json.dumps(verdict))
    else:
        print(format_backup_status_line(verdict))
    if verdict["status"] != "ok":
        sys.exit(1)


def _download_and_verify(c, picked, workpath: Path) -> Path:
    """Download snapshot from Yadisk, decompress, integrity-check. Returns path to restored DB."""
    archive = workpath / picked[0]
    restored = workpath / "restored.db"
    remote_path = f"{BACKUP_RCLONE_REMOTE}:{BACKUP_RCLONE_PATH}/{picked[0]}"
    c.run(f"rclone copyto {shlex.quote(remote_path)} {shlex.quote(str(archive))}")
    c.run(f"zstd -q -d {shlex.quote(str(archive))} -o {shlex.quote(str(restored))}")
    check = subprocess.run(
        ["sqlite3", str(restored), "PRAGMA integrity_check"],
        capture_output=True,
        text=True,
        check=False,
    )
    if check.returncode != 0 or check.stdout.strip() != "ok":
        sys.stderr.write(
            f"integrity_check FAILED on {picked[0]}; "
            f"data/dinary.db NOT touched.\n"
            f"  stdout: {check.stdout.strip() or '(empty)'}\n"
            f"  stderr: {check.stderr.strip() or '(empty)'}\n",
        )
        sys.exit(1)
    return restored


@task(name="backup-cloud-restore")
def restore_from_yadisk(c, snapshot="latest", list_only=False, yes=False, no_resync=False):
    """Restore DB from Yandex.Disk snapshots written by the Litestream replica (VM2).

    The replica continuously pushes compressed SQLite snapshots to Yandex.Disk
    via ``rclone``.  This task downloads and restores from that same remote,
    making it the DR counterpart of the replica's backup job.

    **Run on the server** (``ssh ubuntu@dinary && cd ~/dinary``), not locally.
    Writes to ``./data/dinary.db`` relative to the cwd.

    After the restore, automatically resyncs the Litestream replica
    (``inv replica-resync``) so its WAL position matches the restored DB.
    Skip with ``--no-resync`` if ``DINARY_REPLICA_HOST`` is not configured
    or the replica is already stopped.

    Flags:
        --snapshot DATE   pick by date prefix (e.g. ``2026-04-22``). Default ``latest``.
        --list-only       enumerate snapshots and exit without writing.
        --yes             skip the "type yes to proceed" gate.
        --no-resync       skip automatic replica resync after restore.
    """
    assert_local_binaries(["rclone", "sqlite3", "zstd"])

    snapshots = yadisk_list_snapshots()
    if not snapshots:
        sys.stderr.write(
            f"No snapshots found at {BACKUP_RCLONE_REMOTE}:{BACKUP_RCLONE_PATH}/.\n",
        )
        sys.exit(1)

    if list_only:
        print_snapshot_list(snapshots)
        return

    picked = pick_snapshot(snapshots, snapshot)
    if picked is None:
        sys.stderr.write(f"No snapshot matches --snapshot={snapshot!r}.\n")
        print_snapshot_list(snapshots, stream=sys.stderr)
        sys.exit(1)

    target_db = Path("data/dinary.db")
    if target_db.exists() and target_db.stat().st_size > 0 and not yes:
        _prompt_restore_confirmation(target_db, picked)

    with tempfile.TemporaryDirectory() as workdir:
        restored = _download_and_verify(c, picked, Path(workdir))

        target_db.parent.mkdir(parents=True, exist_ok=True)
        if target_db.exists():
            ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%MZ")
            preserved = target_db.with_name(f"dinary.db.before-restore-{ts}")
            target_db.rename(preserved)
            print(f"Previous data/dinary.db saved as data/{preserved.name}")

        for wal_file in (
            target_db.with_suffix(".db-wal"),
            target_db.with_suffix(".db-shm"),
        ):
            if wal_file.exists():
                wal_file.unlink()
                print(f"Removed stale WAL file {wal_file.name}")
        shutil.move(str(restored), str(target_db))

    print(f"Restored data/dinary.db from {picked[0]}")

    if no_resync:
        print("=== --no-resync set: skipping replica resync. ===")
        return

    if not _env().get("DINARY_REPLICA_HOST"):
        print("=== DINARY_REPLICA_HOST not set: skipping replica resync. ===")
        return
    print(f"=== Replica {replica_host()} detected — resyncing to match restored DB ===")
    replica_resync(c)
