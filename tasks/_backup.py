"""Backup, replica, and Litestream tasks."""

import base64
import getpass
import json as _json
import re as _re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import UTC
from datetime import datetime as _dt
from pathlib import Path

from invoke import task

from dinary.tools.backup_retention import _make_pattern as _backup_make_pattern

from ._common import (
    BACKUP_FILENAME_PREFIX,
    BACKUP_FILENAME_SUFFIX,
    BACKUP_RCLONE_PATH,
    BACKUP_RCLONE_REMOTE,
    BACKUP_RETENTION_DAILY,
    BACKUP_RETENTION_MONTHLY,
    BACKUP_RETENTION_SCRIPT_PATH,
    BACKUP_RETENTION_WEEKLY,
    BACKUP_SCRIPT_PATH,
    BACKUP_SERVICE_PATH,
    BACKUP_STALE_HOURS,
    BACKUP_TIMER_PATH,
    LITESTREAM_SERVICE,
    LOCAL_LITESTREAM_CONFIG_PATH,
    LOCAL_LITESTREAM_EXAMPLE_PATH,
    REMOTE_LITESTREAM_CONFIG_PATH,
    REPLICA_DB_NAME,
    REPLICA_LITESTREAM_DIR,
    _build_setup_swap_script,
    _build_ssh_tailscale_only_script,
    _create_service,
    _litestream_install_script,
    _replica_host,
    _ssh,
    _ssh_replica,
    _ssh_replica_capture_bytes,
    _ssh_sudo,
    _write_remote_file,
    _write_remote_replica_file,
)

# ---------------------------------------------------------------------------
# Yandex rclone helpers
# ---------------------------------------------------------------------------


def _replica_has_working_yandex_remote():
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
        ["ssh", _replica_host(), probe],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.stderr:
        sys.stderr.write(result.stderr)
    return result.returncode == 0


def _prompt_yandex_credentials():
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


def _install_yandex_rclone_remote(login: str, app_password: str) -> None:
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
    ``yandex`` remote, so the next ``inv backup-cloud-setup``
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
        ["ssh", _replica_host(), outer],
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
            "Re-run `inv backup-cloud-setup` to try again; it will\n"
            "prompt for credentials fresh.\n",
        )
        sys.exit(proc.returncode)


def _ensure_yandex_rclone_configured(c):  # noqa: ARG001
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
    if _replica_has_working_yandex_remote():
        print("yandex: remote already configured and healthy — skipping prompt.")
        return
    login, app_password = _prompt_yandex_credentials()
    _install_yandex_rclone_remote(login, app_password)
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


def _build_setup_replica_backup_packages_script() -> str:
    """Emit the apt step of the backup bootstrap on VM2.

    Installs the three binaries the daily backup pipeline shells out
    to (``rclone`` for uploads, ``sqlite3`` for PRAGMA integrity_check,
    ``zstd`` for compression). Kept separate from the replica's own
    apt step so ``inv setup-replica`` can stay minimal — a box that is
    only an SFTP sink does not need rclone.

    ``DEBIAN_FRONTEND=noninteractive`` mirrors
    :func:`_build_setup_replica_packages_script`: without it, apt on
    a fresh cloud image can block on a postfix / grub debconf prompt
    that nobody is around to answer.
    """
    return (
        "sudo bash <<'DINARY_BACKUP_PKG_EOF'\n"
        "set -euo pipefail\n"
        "export DEBIAN_FRONTEND=noninteractive\n"
        "apt-get update -qq\n"
        "apt-get install -y -qq rclone sqlite3 zstd\n"
        "DINARY_BACKUP_PKG_EOF\n"
    )


def _build_backup_script() -> str:
    """Emit the ``/usr/local/bin/dinary-backup`` bash script.

    Daily flow on VM2:

    1. ``litestream restore`` materializes the local replica tree at
       ``<REPLICA_LITESTREAM_DIR>/<REPLICA_DB_NAME>`` into a plain
       SQLite file in a throwaway workdir.
    2. ``PRAGMA integrity_check`` on the restored file. A failure here
       aborts the run without touching Yandex — we refuse to
       overwrite the remote history with a visibly corrupt snapshot.
    3. ``zstd -19`` compresses (ratio is near-optimal on SQLite's
       highly repetitive page layout, CPU cost negligible on a
       <1 MB input).
    4. ``rclone copyto`` uploads to
       ``<BACKUP_RCLONE_REMOTE>:<BACKUP_RCLONE_PATH>/`` under a
       lexicographically-chronological filename
       ``dinary-YYYY-MM-DDTHHMMZ.db.zst``.
    5. Calls ``/usr/local/bin/dinary-backup-retention`` to prune per
       the GFS policy.

    ``set -euo pipefail`` + ``trap 'rm -rf "$WORKDIR"' EXIT`` so a
    failure never leaks multi-MB temp files on the 46 GB VM2 disk,
    and never creates an upload from a half-restored DB.
    """
    replica_path = f"{REPLICA_LITESTREAM_DIR}/{REPLICA_DB_NAME}"
    remote_prefix = f"{BACKUP_RCLONE_REMOTE}:{BACKUP_RCLONE_PATH}"
    return f"""#!/usr/bin/env bash
# Managed by inv backup-cloud-setup. Overwritten on every re-apply.
# Daily backup: Litestream replica -> plain .db -> zstd -> Yandex.Disk.
set -euo pipefail

WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"' EXIT

SNAP="$WORKDIR/dinary.db"
CFG="$WORKDIR/litestream-restore.yml"

cat > "$CFG" <<YAML
dbs:
  - path: $SNAP
    replicas:
      - type: file
        path: {replica_path}
YAML

litestream restore -config "$CFG" "$SNAP"

if ! sqlite3 "$SNAP" 'PRAGMA integrity_check' | grep -qx 'ok'; then
    echo "integrity_check FAILED on restored snapshot" >&2
    exit 1
fi

zstd -q -19 "$SNAP" -o "$SNAP.zst"

TS=$(date -u +%Y-%m-%dT%H%MZ)
REMOTE="{remote_prefix}/{BACKUP_FILENAME_PREFIX}$TS{BACKUP_FILENAME_SUFFIX}"
rclone copyto "$SNAP.zst" "$REMOTE"

{BACKUP_RETENTION_SCRIPT_PATH} \\
    --remote "{remote_prefix}/" \\
    --prefix "{BACKUP_FILENAME_PREFIX}" \\
    --suffix "{BACKUP_FILENAME_SUFFIX}" \\
    --daily {BACKUP_RETENTION_DAILY} \\
    --weekly {BACKUP_RETENTION_WEEKLY} \\
    --monthly {BACKUP_RETENTION_MONTHLY}
"""


def _build_backup_service_unit() -> str:
    """Emit the ``dinary-backup.service`` systemd unit (oneshot).

    ``User=ubuntu`` so the run uses the same ``~/.config/rclone/
    rclone.conf`` that the operator configured interactively; no
    root/ubuntu config split. ``Type=oneshot`` is the natural shape
    for a pipeline that must either finish cleanly or show up failed
    in the journal — no long-running daemon state to keep alive.

    ``Nice`` + ``IOSchedulingClass`` push the job below the
    ``litestream.service`` replica-producer on VM1's SFTP endpoint,
    so a scheduled backup never contends with the running hot-
    replication feed that it depends on.
    """
    return (
        "# Managed by inv backup-cloud-setup. Overwritten on every re-apply.\n"
        "[Unit]\n"
        "Description=Back up dinary replica to Yandex.Disk\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        "User=ubuntu\n"
        f"ExecStart={BACKUP_SCRIPT_PATH}\n"
        "Nice=10\n"
        "IOSchedulingClass=best-effort\n"
        "IOSchedulingPriority=7\n"
    )


def _build_backup_timer_unit() -> str:
    """Emit the ``dinary-backup.timer`` systemd unit.

    03:17 UTC + 30m jitter: off every regular-looking hour boundary
    (so we do not collide with the top-of-hour Litestream snapshot
    cadence on VM1, which is the producer of the replica we read),
    and late enough that most operator timezones are asleep.

    ``Persistent=true`` → if VM2 happened to be down through the
    scheduled slot, the missed run triggers at next boot. Otherwise
    a reboot-on-the-hour would silently create a 24 h retention gap.
    """
    return (
        "# Managed by inv backup-cloud-setup. Overwritten on every re-apply.\n"
        "[Unit]\n"
        "Description=Daily dinary off-site backup\n"
        "\n"
        "[Timer]\n"
        "OnCalendar=*-*-* 03:17:00\n"
        "RandomizedDelaySec=30m\n"
        "Persistent=true\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


# ---------------------------------------------------------------------------
# Snapshot inventory helpers
# ---------------------------------------------------------------------------


def _parse_snapshot_lsjson(raw):
    """Turn ``rclone lsjson`` output into ``[(name, size_bytes), ...]``.

    Pure parser, no I/O. Both the local-rclone reader
    (:func:`_yadisk_list_snapshots`) and the over-SSH reader
    (:func:`_replica_list_snapshots`) go through this helper so a
    change to the filename regex or to the "ignore noise" rule
    cannot drift between restore (local) and freshness monitoring
    (replica). Always sorted oldest-first so callers can reach for
    ``result[-1]`` to get the newest deterministically.

    Anything whose filename does not match the backup pattern is
    silently dropped so human-uploaded noise in the same Yandex
    folder cannot break the inventory.
    """
    entries = _json.loads(raw)
    pattern = _backup_make_pattern(BACKUP_FILENAME_PREFIX, BACKUP_FILENAME_SUFFIX)
    result = []
    for entry in entries:
        name = entry.get("Name", "")
        if pattern.match(name):
            result.append((name, int(entry.get("Size", 0))))
    result.sort(key=lambda x: x[0])
    return result


def _yadisk_list_snapshots():
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
    return _parse_snapshot_lsjson(raw)


def _replica_list_snapshots():
    """Like :func:`_yadisk_list_snapshots` but asks VM2 over SSH.

    Used by :func:`backup_status` so the monitoring path reuses the
    already-configured ``yandex:`` remote on VM2. The laptop can
    then run freshness checks from cron without keeping its own
    Yandex WebDAV credentials.
    """
    raw = _ssh_replica_capture_bytes(
        f"rclone lsjson {BACKUP_RCLONE_REMOTE}:{BACKUP_RCLONE_PATH}/ --files-only",
    ).decode("utf-8")
    return _parse_snapshot_lsjson(raw)


def _parse_snapshot_timestamp(name):
    """Extract the UTC datetime encoded in a backup filename.

    Filenames are ``dinary-YYYY-MM-DDTHHMMZ.db.zst`` — the timestamp
    lives in the name itself and is the single source of truth for
    "when was this backup produced". Using filename timestamps rather
    than ``rclone``'s ``ModTime`` means freshness checks are
    independent of Yandex-side clock skew and of any metadata
    rewrites a future rclone version might do.
    """
    pattern = _re.compile(
        _re.escape(BACKUP_FILENAME_PREFIX)
        + r"(\d{4})-(\d{2})-(\d{2})T(\d{2})(\d{2})Z"
        + _re.escape(BACKUP_FILENAME_SUFFIX)
        + "$",
    )
    match = pattern.match(name)
    if match is None:
        return None
    year, month, day, hour, minute = (int(g) for g in match.groups())
    return _dt(year, month, day, hour, minute, tzinfo=UTC)


def _check_backup_freshness(snapshots, now, max_age_hours):
    """Compute the freshness verdict for ``inv backup-cloud-status``.

    Pure helper so tests can pin the ok/stale/empty branches without
    any SSH/rclone plumbing. Returns a dict ready for both human-
    and JSON output paths.

    Keys:
        ``status``           ``ok`` | ``stale`` | ``empty``
        ``newest``           filename of the latest snapshot (or None)
        ``age_hours``        float, hours between now and the filename
                             timestamp (or None)
        ``size_bytes``       int (or None)
        ``threshold_hours``  float, the ``max_age_hours`` input
    """
    if not snapshots:
        return {
            "status": "empty",
            "newest": None,
            "age_hours": None,
            "size_bytes": None,
            "threshold_hours": float(max_age_hours),
        }
    name, size = snapshots[-1]
    ts = _parse_snapshot_timestamp(name)
    if ts is None:
        return {
            "status": "stale",
            "newest": name,
            "age_hours": None,
            "size_bytes": size,
            "threshold_hours": float(max_age_hours),
        }
    age_hours = (now - ts).total_seconds() / 3600.0
    status = "ok" if age_hours <= max_age_hours else "stale"
    return {
        "status": status,
        "newest": name,
        "age_hours": age_hours,
        "size_bytes": size,
        "threshold_hours": float(max_age_hours),
    }


def _format_backup_status_line(verdict):
    """Render one human-readable summary line for ``inv backup-cloud-status``.

    Kept separate from the task so the laptop cron wrapper can log
    the exact same line the operator would see, and so tests can pin
    the wording without importing invoke's ``Context``.
    """
    threshold = verdict["threshold_hours"]
    if verdict["status"] == "empty":
        return (
            f"STALE: no snapshots on "
            f"{BACKUP_RCLONE_REMOTE}:{BACKUP_RCLONE_PATH}/ "
            f"(threshold: {threshold:g}h)"
        )
    name = verdict["newest"]
    size = verdict["size_bytes"]
    size_kb = (size or 0) / 1024
    age = verdict["age_hours"]
    if age is None:
        return (
            f"STALE: newest {name} has un-parseable timestamp "
            f"({size_kb:,.1f} KB, threshold: {threshold:g}h)"
        )
    tag = "OK" if verdict["status"] == "ok" else "STALE"
    return (
        f"{tag}: newest {name}, age {age:.1f}h, size {size_kb:,.1f} KB (threshold: {threshold:g}h)"
    )


def _pick_snapshot(snapshots, key):
    """Resolve the ``--snapshot`` CLI arg to one inventory entry.

    ``key='latest'`` returns the newest snapshot (sorts are
    chronological by filename).

    Any other value is treated as a date-prefix match against the
    ``YYYY-MM-DD`` portion of the filename so the operator can type
    ``--snapshot 2026-03-15`` and get the run stored as
    ``dinary-2026-03-15T0317Z.db.zst`` without memorizing the time
    suffix. Returns ``None`` when nothing matches so callers can
    print a useful error with the full inventory.
    """
    if not snapshots:
        return None
    if key == "latest":
        return snapshots[-1]
    needle = f"{BACKUP_FILENAME_PREFIX}{key}"
    for name, size in snapshots:
        if name.startswith(needle):
            return (name, size)
    return None


def _print_snapshot_list(snapshots, stream=None):
    """Chronological dump, newest first, with human-readable sizes.

    Used by both ``--list`` (operator discovery) and the "no match"
    error path (so a typo in ``--snapshot`` surfaces the actual
    available keys next to the error message).
    """
    stream = stream if stream is not None else sys.stdout
    for name, size in reversed(snapshots):
        kb = size / 1024
        stream.write(f"  {name}  ({kb:,.1f} KB)\n")


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
    row_count = _sqlite_row_count(target_db)
    size_kb = target_db.stat().st_size / 1024
    mtime = _dt.fromtimestamp(target_db.stat().st_mtime, tz=UTC).strftime(
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


def _sqlite_row_count(db_path):
    """Cheap "how many expenses are about to vanish" sanity number.

    Returns zero on any SQLite error (schema mismatch, file locked,
    table absent in a very-fresh-bootstrap DB). The number is cosmetic
    — it feeds the confirmation prompt — so failing soft is better
    than aborting the restore because the count query tripped on an
    edge-case file.
    """
    try:
        with sqlite3.connect(db_path) as conn:
            cur = conn.execute("SELECT COUNT(*) FROM expenses")
            return cur.fetchone()[0]
    except sqlite3.Error:
        return 0


def _assert_local_binaries(names):
    """Bail out with an actionable message if any required CLI is missing.

    Checked up-front in :func:`restore_from_yadisk` so the operator
    sees one consolidated "install X" error rather than discovering
    a missing tool mid-pipeline after the snapshot has already been
    downloaded.
    """
    missing = [name for name in names if shutil.which(name) is None]
    if not missing:
        return
    sys.stderr.write(
        "Missing local tools: " + ", ".join(missing) + ".\n"
        "  Ubuntu/Debian: sudo apt install " + " ".join(missing) + "\n"
        "  macOS: brew install " + " ".join(missing) + "\n"
        "Also ensure `rclone config` has run and a remote named "
        f"{BACKUP_RCLONE_REMOTE!r} exists (one-time OAuth browser click).\n",
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


@task(name="setup-replica")
def setup_replica(c, swap_size_gb=1):
    """Bootstrap a Litestream SFTP replica host (VM2).

    Distinct from :func:`setup` because the replica is intentionally
    minimal: no Python app, no systemd service for ``dinary``, no
    public tunnel. Its only job is to accept WAL segments over SFTP
    from VM1's ``litestream.service`` and hold them until the laptop
    pulls the snapshot.

    Preconditions (not auto-provisioned by this task):

    * ``DINARY_REPLICA_HOST`` is set in ``.deploy/.env`` to the SSH
      target of VM2 (e.g. ``ubuntu@dinary-replica`` via Tailscale
      MagicDNS).
    * Tailscale is installed and logged in on VM2. We deliberately
      do not automate ``tailscale up`` because its OAuth flow needs
      a human-approved browser click, and stashing a long-lived
      auth key in this task would be a much larger attack surface
      than the one-off manual step.
    * Current shell on the operator machine can already ``ssh
      $DINARY_REPLICA_HOST`` (a second terminal for break-glass is
      strongly recommended — the final step flips public TCP/22
      closed, mirroring :func:`ssh_tailscale_only`).

    Idempotent: every step is a pure-function shell block that
    short-circuits on re-apply (apt no-op on already-installed
    packages, ``mkdir -p`` plus explicit chown/chmod, the swap
    builder's own ``swapon --show`` guard, the ssh-tailscale-only
    drop-in rewrite).

    Flags:
        --swap-size-gb N   replica swap size in gigabytes (default 1,
                           matches the Always Free VM2 profile).
    """
    size = int(swap_size_gb)
    print(f"=== Installing baseline packages on {_replica_host()} ===")
    _ssh_replica(c, _build_setup_replica_packages_script())
    print(f"=== Provisioning {REPLICA_LITESTREAM_DIR} ===")
    _ssh_replica(c, _build_setup_replica_litestream_dir_script())
    print("=== Allocating swap ===")
    _ssh_replica(c, _build_setup_swap_script(size_gb=size))
    print("=== Restricting sshd to Tailscale + loopback ===")
    _ssh_replica(c, _build_ssh_tailscale_only_script())
    print("=== Replica bootstrap done ===")


@task(name="backup-cloud-setup")
def setup_replica_backup(c):
    """Add the daily Yandex.Disk backup role on top of an already-
    provisioned Litestream replica host (VM2).

    This is a *layer on top of* :func:`setup_replica`, not a
    replacement for it. ``setup_replica`` owns the Litestream
    receiver (SFTP ingestion of WAL segments from VM1). This task
    owns the daily "take the current replica, validate it, upload
    a compressed snapshot to Yandex.Disk, prune by GFS" pipeline.

    Idempotent. apt installs are no-op on re-apply, rclone-remote
    bootstrap is a no-op once ``yandex:`` exists, scripts and
    systemd units are overwritten, the timer's ``enable --now`` is
    harmless to re-run.
    """
    replica = _replica_host()

    print(f"=== Installing apt packages (rclone, sqlite3, zstd) on {replica} ===")
    _ssh_replica(c, _build_setup_replica_backup_packages_script())

    print("=== Installing Litestream binary on replica ===")
    _ssh_replica(c, _litestream_install_script())

    print("=== Ensuring rclone 'yandex' remote is configured ===")
    _ensure_yandex_rclone_configured(c)

    print(f"=== Preparing {BACKUP_RCLONE_REMOTE}:{BACKUP_RCLONE_PATH}/ ===")
    _ssh_replica(
        c,
        f"rclone mkdir {BACKUP_RCLONE_REMOTE}:{BACKUP_RCLONE_PATH} && "
        f"rclone lsf {BACKUP_RCLONE_REMOTE}:{BACKUP_RCLONE_PATH}/ || true",
    )

    print(f"=== Writing {BACKUP_SCRIPT_PATH} ===")
    _write_remote_replica_file(c, BACKUP_SCRIPT_PATH, _build_backup_script())
    _ssh_replica(c, f"sudo chmod 0755 {BACKUP_SCRIPT_PATH}")

    print(f"=== Writing {BACKUP_RETENTION_SCRIPT_PATH} ===")
    _retention_src = (
        Path(__file__).parent.parent / "src/dinary/tools/backup_retention.py"
    ).read_text()
    _write_remote_replica_file(c, BACKUP_RETENTION_SCRIPT_PATH, _retention_src)
    _ssh_replica(c, f"sudo chmod 0755 {BACKUP_RETENTION_SCRIPT_PATH}")

    print("=== Installing systemd service + timer ===")
    _write_remote_replica_file(c, BACKUP_SERVICE_PATH, _build_backup_service_unit())
    _write_remote_replica_file(c, BACKUP_TIMER_PATH, _build_backup_timer_unit())
    _ssh_replica(c, "sudo systemctl daemon-reload")
    _ssh_replica(c, "sudo systemctl enable --now dinary-backup.timer")

    print("=== Running an initial backup to verify the pipeline end-to-end ===")
    _ssh_replica(
        c,
        "sudo systemctl start dinary-backup.service && "
        "sudo journalctl -u dinary-backup.service -n 40 --no-pager",
    )
    print("=== backup-cloud-setup done ===")


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
    snapshots = _replica_list_snapshots()
    now = _dt.now(tz=UTC)
    verdict = _check_backup_freshness(snapshots, now, threshold)
    if json_output:
        print(_json.dumps(verdict))
    else:
        print(_format_backup_status_line(verdict))
    if verdict["status"] != "ok":
        sys.exit(1)


@task(name="backup-cloud-restore")
def restore_from_yadisk(c, snapshot="latest", list_only=False, yes=False):
    """Restore ``data/dinary.db`` from an off-site Yandex.Disk backup.

    **Local-only task.** Writes to ``./data/dinary.db`` relative to the cwd.

    Flags:
        --snapshot DATE   pick by date prefix (e.g. ``2026-04-22``). Default ``latest``.
        --list-only       enumerate snapshots and exit without writing.
        --yes             skip the "type yes to proceed" gate.

    Prod recovery runbook:

    .. code-block:: bash

        ssh ubuntu@dinary
        sudo systemctl stop dinary litestream
        cd ~/dinary
        inv backup-cloud-restore --snapshot 2026-03-15
        sudo systemctl start litestream dinary
    """
    _assert_local_binaries(["rclone", "sqlite3", "zstd"])

    snapshots = _yadisk_list_snapshots()
    if not snapshots:
        sys.stderr.write(
            f"No snapshots found at {BACKUP_RCLONE_REMOTE}:{BACKUP_RCLONE_PATH}/.\n",
        )
        sys.exit(1)

    if list_only:
        _print_snapshot_list(snapshots)
        return

    picked = _pick_snapshot(snapshots, snapshot)
    if picked is None:
        sys.stderr.write(f"No snapshot matches --snapshot={snapshot!r}.\n")
        _print_snapshot_list(snapshots, stream=sys.stderr)
        sys.exit(1)

    target_db = Path("data/dinary.db")
    if target_db.exists() and target_db.stat().st_size > 0 and not yes:
        _prompt_restore_confirmation(target_db, picked)

    with tempfile.TemporaryDirectory() as workdir:
        workpath = Path(workdir)
        archive = workpath / picked[0]
        restored = workpath / "restored.db"

        remote_path = f"{BACKUP_RCLONE_REMOTE}:{BACKUP_RCLONE_PATH}/{picked[0]}"
        c.run(f"rclone copyto {shlex.quote(remote_path)} {shlex.quote(str(archive))}")
        c.run(
            f"zstd -q -d {shlex.quote(str(archive))} -o {shlex.quote(str(restored))}",
        )

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

        target_db.parent.mkdir(parents=True, exist_ok=True)
        if target_db.exists():
            ts = _dt.now(tz=UTC).strftime("%Y%m%dT%H%MZ")
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


@task(name="litestream-setup")
def litestream_setup(c):
    """Install Litestream on VM 1 and start the replicator sidecar.

    One-time bootstrap for the Phase 2 hot replica. This is a passive
    sidecar: it reads WAL segments from ``data/dinary.db`` and ships
    them to the SFTP replica declared in ``.deploy/litestream.yml``.

    The task is idempotent: re-running it upgrades Litestream, re-
    uploads the config, and restarts the sidecar.
    """
    if not Path(LOCAL_LITESTREAM_CONFIG_PATH).exists():
        print(
            f"No {LOCAL_LITESTREAM_CONFIG_PATH} locally.\n"
            f"Copy {LOCAL_LITESTREAM_EXAMPLE_PATH} to {LOCAL_LITESTREAM_CONFIG_PATH} "
            "and fill in the SFTP target, then retry.",
            file=sys.stderr,
        )
        sys.exit(1)

    print("=== Installing Litestream binary ===")
    _ssh(c, _litestream_install_script())

    print(f"=== Uploading {LOCAL_LITESTREAM_CONFIG_PATH} to {REMOTE_LITESTREAM_CONFIG_PATH} ===")
    content = Path(LOCAL_LITESTREAM_CONFIG_PATH).read_text(encoding="utf-8")
    _write_remote_file(c, REMOTE_LITESTREAM_CONFIG_PATH, content)
    _ssh_sudo(
        c,
        f"bash -c 'chown root:root {REMOTE_LITESTREAM_CONFIG_PATH} "
        f"&& chmod 644 {REMOTE_LITESTREAM_CONFIG_PATH}'",
    )

    print("=== Creating litestream systemd service ===")
    _create_service(c, "litestream", LITESTREAM_SERVICE)

    print("=== Checking status ===")
    _ssh(
        c,
        "sleep 3 && systemctl is-active litestream && "
        "sudo journalctl -u litestream -n 20 --no-pager",
    )


@task(name="litestream-status")
def litestream_status(c):
    """Show the remote Litestream sidecar's health and replica state.

    Prints the systemd unit status, the most recent journal lines,
    and the replicator's own view of the managed DB.
    """
    _ssh(c, "systemctl status litestream --no-pager || true")
    _ssh(c, "sudo journalctl -u litestream -n 30 --no-pager || true")
    _ssh(c, f"litestream databases -config {REMOTE_LITESTREAM_CONFIG_PATH} || true")
