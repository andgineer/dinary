"""Pure helpers for backup snapshot inventory and freshness checks.

No SSH, no invoke Context, no .deploy/.env dependency.
I/O callers (_yadisk_list_snapshots, _replica_list_snapshots) live in
tasks/_backup.py and use these helpers after fetching raw rclone output.
"""

import json
import re
import shutil
import sqlite3
import sys
from datetime import UTC, datetime

from dinary.tools.backup_retention import _make_pattern

# ---------------------------------------------------------------------------
# Naming / storage constants
# ---------------------------------------------------------------------------

BACKUP_FILENAME_PREFIX = "dinary-"
BACKUP_FILENAME_SUFFIX = ".db.zst"

BACKUP_RCLONE_REMOTE = "yandex"
# Nested under ``Backup/`` because the operator's Yandex.Disk already
# has a ``Backup`` folder used by other tools — keeping dinary under a
# leaf ``dinary/`` avoids colliding with ad-hoc human uploads in the
# same namespace and matches the existing filesystem convention.
# ``rclone mkdir`` is idempotent and will create the ``dinary`` leaf
# inside the existing ``Backup`` parent on first run.
BACKUP_RCLONE_PATH = "Backup/dinary"

# Freshness threshold for `inv backup-cloud-status`. The daily systemd timer
# fires at 03:17 UTC with 30 min jitter, so a healthy snapshot is
# always <= 24h30m old. 26h gives a ~1h30m buffer for a single missed
# jitter window without false-alerting. A stale snapshot for >26h
# means the pipeline silently stopped producing.
BACKUP_STALE_HOURS = 26

# ---------------------------------------------------------------------------
# Snapshot inventory helpers
# ---------------------------------------------------------------------------


def parse_snapshot_lsjson(raw):
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
    entries = json.loads(raw)
    pattern = _make_pattern(BACKUP_FILENAME_PREFIX, BACKUP_FILENAME_SUFFIX)
    result = []
    for entry in entries:
        name = entry.get("Name", "")
        if pattern.match(name):
            result.append((name, int(entry.get("Size", 0))))
    result.sort(key=lambda x: x[0])
    return result


def parse_snapshot_timestamp(name):
    """Extract the UTC datetime encoded in a backup filename.

    Filenames are ``dinary-YYYY-MM-DDTHHMMZ.db.zst`` — the timestamp
    lives in the name itself and is the single source of truth for
    "when was this backup produced". Using filename timestamps rather
    than ``rclone``'s ``ModTime`` means freshness checks are
    independent of Yandex-side clock skew and of any metadata
    rewrites a future rclone version might do.
    """
    pattern = re.compile(
        re.escape(BACKUP_FILENAME_PREFIX)
        + r"(\d{4})-(\d{2})-(\d{2})T(\d{2})(\d{2})Z"
        + re.escape(BACKUP_FILENAME_SUFFIX)
        + "$",
    )
    match = pattern.match(name)
    if match is None:
        return None
    year, month, day, hour, minute = (int(g) for g in match.groups())
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def check_backup_freshness(snapshots, now, max_age_hours):
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
    ts = parse_snapshot_timestamp(name)
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


def format_backup_status_line(verdict):
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


def pick_snapshot(snapshots, key):
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


def print_snapshot_list(snapshots, stream=None):
    """Chronological dump, newest first, with human-readable sizes.

    Used by both ``--list`` (operator discovery) and the "no match"
    error path (so a typo in ``--snapshot`` surfaces the actual
    available keys next to the error message).
    """
    stream = stream if stream is not None else sys.stdout
    for name, size in reversed(snapshots):
        kb = size / 1024
        stream.write(f"  {name}  ({kb:,.1f} KB)\n")


def sqlite_row_count(db_path):
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


def assert_local_binaries(names):
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
