import argparse
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import lmdb

from dinary_analytics.paths import ANALYTICS_DB_PATH

FILENAME_PREFIX = "dinary-analytics-"
FILENAME_SUFFIX = ".db.zst"


def _default_filename() -> str:
    ts = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H%MZ")
    return f"{FILENAME_PREFIX}{ts}{FILENAME_SUFFIX}"


def backup_to_file(output: Path) -> None:
    if not ANALYTICS_DB_PATH.exists():
        sys.stderr.write(f"analytics.db not found at {ANALYTICS_DB_PATH} — nothing to backup.\n")
        sys.exit(1)
    env = lmdb.open(str(ANALYTICS_DB_PATH), max_dbs=1, readonly=True)
    try:
        with tempfile.TemporaryDirectory() as workdir:
            env.copy(workdir)
            data_mdb = Path(workdir) / "data.mdb"
            result = subprocess.run(
                ["zstd", "-q", "-19", str(data_mdb), "-o", str(output)],
                check=False,
            )
            if result.returncode != 0:
                sys.stderr.write("zstd compression failed.\n")
                sys.exit(1)
    finally:
        env.close()
    print(f"analytics.db → {output}  ({output.stat().st_size / 1024:.1f} KB)")


def restore_from_file(src: Path) -> None:
    if not src.exists():
        sys.stderr.write(f"Backup file not found: {src}\n")
        sys.exit(1)
    with tempfile.TemporaryDirectory() as workdir:
        data_mdb = Path(workdir) / "data.mdb"
        result = subprocess.run(
            ["zstd", "-q", "-d", str(src), "-o", str(data_mdb)],
            check=False,
        )
        if result.returncode != 0:
            sys.stderr.write("zstd decompression failed.\n")
            sys.exit(1)
        ANALYTICS_DB_PATH.mkdir(parents=True, exist_ok=True)
        lock = ANALYTICS_DB_PATH / "lock.mdb"
        if lock.exists():
            lock.unlink()
        dest = ANALYTICS_DB_PATH / "data.mdb"
        if dest.exists():
            ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%MZ")
            dest.rename(dest.with_name(f"data.mdb.before-restore-{ts}"))
        shutil.move(str(data_mdb), str(dest))
    print(f"Restored analytics.db from {src}")


def main() -> None:
    parser = argparse.ArgumentParser(description="analytics.db backup/restore")
    sub = parser.add_subparsers(dest="command")

    bp = sub.add_parser("backup", help="Backup analytics.db to a zstd-compressed file")
    bp.add_argument("--output", default=None, help="Output path (default: auto-named in CWD)")

    rp = sub.add_parser("restore", help="Restore analytics.db from a zstd-compressed file")
    rp.add_argument("--file", required=True, help="Path to backup file (.db.zst)")

    args = parser.parse_args()

    if args.command == "backup":
        dest = Path(args.output) if args.output else Path(_default_filename())
        backup_to_file(dest)
    elif args.command == "restore":
        restore_from_file(Path(args.file))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
