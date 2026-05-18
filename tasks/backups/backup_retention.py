#!/usr/bin/env python3
"""GFS retention for dinary Yandex.Disk backups.

Uploaded to VM2 as /usr/local/bin/dinary-backup-retention by
``inv setup-replica``. Called from the daily ``dinary-backup``
bash script with deployment-specific CLI args.

Pure stdlib — VM2 has no dinary venv.
"""

import argparse
import datetime as dt
import re
import subprocess
import sys


def _make_pattern(prefix: str, suffix: str) -> re.Pattern:
    return re.compile(
        "^" + re.escape(prefix) + r"(\d{4}-\d{2}-\d{2})T\d{4}Z" + re.escape(suffix) + "$",
    )


def list_snapshots(remote: str, pattern: re.Pattern) -> list:
    """Return [(date, name), ...] sorted oldest-first."""
    out = subprocess.check_output(["rclone", "lsf", remote, "--files-only"], text=True)
    snaps = []
    for line in out.splitlines():
        name = line.strip()
        m = pattern.match(name)
        if m:
            snaps.append((dt.date.fromisoformat(m.group(1)), name))
    snaps.sort()
    return snaps


def pick_keepers(snaps, *, daily: int, weekly: int, monthly: int) -> set:
    """Return the set of filenames that must NOT be deleted (GFS policy).

    Buckets overlap — a snapshot is pruned only when it belongs to
    no keeper bucket.

    * Last ``daily`` snapshots by date.
    * Newest-per-ISO-week for the last ``weekly`` weeks.
    * Newest-per-calendar-month for the last ``monthly`` months.
    * One per calendar year, forever (closed years are immutable;
      any drift between two yearly snapshots of the same closed year
      signals corruption worth preserving).
    """
    keepers = set()
    for _, name in snaps[-daily:]:
        keepers.add(name)
    per_week: dict = {}
    for d, name in snaps:
        per_week[d.isocalendar()[:2]] = name
    for key in sorted(per_week)[-weekly:]:
        keepers.add(per_week[key])
    per_month: dict = {}
    for d, name in snaps:
        per_month[(d.year, d.month)] = name
    for key in sorted(per_month)[-monthly:]:
        keepers.add(per_month[key])
    per_year: dict = {}
    for d, name in snaps:
        per_year[d.year] = name
    keepers.update(per_year.values())
    return keepers


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="GFS retention for dinary backups")
    parser.add_argument(
        "--remote",
        required=True,
        help="rclone remote path, e.g. yandex:dinary-backup/",
    )
    parser.add_argument("--prefix", required=True, help="backup filename prefix, e.g. dinary-")
    parser.add_argument("--suffix", required=True, help="backup filename suffix, e.g. .db.zst")
    parser.add_argument("--daily", type=int, required=True)
    parser.add_argument("--weekly", type=int, required=True)
    parser.add_argument("--monthly", type=int, required=True)
    args = parser.parse_args(argv)

    pattern = _make_pattern(args.prefix, args.suffix)
    snaps = list_snapshots(args.remote, pattern)
    keepers = pick_keepers(snaps, daily=args.daily, weekly=args.weekly, monthly=args.monthly)
    to_delete = [name for _, name in snaps if name not in keepers]
    for name in to_delete:
        subprocess.check_call(["rclone", "delete", args.remote + name])
    print(f"kept {len(keepers)}, deleted {len(to_delete)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
