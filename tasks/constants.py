"""Constants for all task modules: paths, service templates, version pins."""

from dinary.tools.backup_snapshots import (
    BACKUP_FILENAME_PREFIX,
    BACKUP_FILENAME_SUFFIX,
    BACKUP_RCLONE_PATH,
    BACKUP_RCLONE_REMOTE,
    BACKUP_STALE_HOURS,
)
from dinary.tools.report_helpers import extract_format_flags, extract_year_month


def get_allowed_doc_languages():
    build_docs_file_name = "scripts/build-docs.sh"
    try:
        with open(build_docs_file_name) as f:
            for line in f:
                if "LANGUAGES=" in line:
                    value = line.split("=", 1)[1].strip()
                    value = value.strip('"').strip("'")
                    langs = value.split()
                    return [lang.strip() for lang in langs]
    except FileNotFoundError:
        print(f"No {build_docs_file_name} file found")
    return ["en", "bg", "de", "es", "fr", "ru"]


ALLOWED_DOC_LANGUAGES = get_allowed_doc_languages()
ALLOWED_VERSION_TYPES = ["release", "bug", "feature"]

REPO_URL = "https://github.com/andgineer/dinary.git"

_UV = "/home/ubuntu/.local/bin/uv"
_DINARY_EXEC = (
    "ExecStart=/bin/sh -c 'exec " + _UV + " run uvicorn dinary.main:app --host {host} --port 8000'"
)

DINARY_SERVICE = (
    "[Unit]\nDescription=dinary\nAfter=network.target\n\n"
    "[Service]\nType=simple\nUser=ubuntu\n"
    "WorkingDirectory=/home/ubuntu/dinary\n"
    "EnvironmentFile=/home/ubuntu/dinary/.deploy/.env\n"
    + _DINARY_EXEC
    + "\nRestart=always\nRestartSec=5\n\n"
    "[Install]\nWantedBy=multi-user.target\n"
)

CLOUDFLARED_SERVICE = """\
[Unit]
Description=Cloudflare Tunnel for dinary
After=network.target

[Service]
Type=simple
User=ubuntu
ExecStart=/usr/bin/cloudflared tunnel run
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""

# systemd unit that runs the Litestream replicator sidecar on VM 1.
# ``User=ubuntu`` matches the app service so the sidecar shares the
# ``data/`` file ACL and can read WAL segments without sudo. The config
# lives at ``/etc/litestream.yml`` (uploaded by ``inv setup-replica``)
# because that is Litestream's default search path and operators editing
# by hand find it where the upstream docs say it should be.
LITESTREAM_SERVICE = """\
[Unit]
Description=Litestream replicator for dinary
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
ExecStart=/usr/bin/litestream replicate -config /etc/litestream.yml
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""

VALID_TUNNELS = ("tailscale", "cloudflare", "none")

LOCAL_ENV_PATH = ".deploy/.env"
LOCAL_ENV_EXAMPLE_PATH = ".deploy.example/.env"
LOCAL_IMPORT_SOURCES_PATH = ".deploy/import_sources.json"
LOCAL_LITESTREAM_CONFIG_PATH = ".deploy/litestream.yml"
LOCAL_LITESTREAM_EXAMPLE_PATH = ".deploy.example/litestream.yml"
REMOTE_DEPLOY_DIR = "/home/ubuntu/dinary/.deploy"
REMOTE_ENV_PATH = f"{REMOTE_DEPLOY_DIR}/.env"
REMOTE_LEGACY_ENV_PATH = "/home/ubuntu/dinary/.env"
REMOTE_IMPORT_SOURCES_PATH = f"{REMOTE_DEPLOY_DIR}/import_sources.json"
REMOTE_LITESTREAM_CONFIG_PATH = "/etc/litestream.yml"

# Pinned Litestream release we install on VM 1. Keeping this as a
# module-level constant — rather than inlined into the shell script
# below — lets tests pin the exact version independently of the
# asset-URL construction, and makes a future upgrade a one-line
# diff that cannot get out of sync with the asset filenames.
LITESTREAM_VERSION = "0.5.1"

REPLICA_LITESTREAM_DIR = "/var/lib/litestream"

# Directory Litestream materializes inside REPLICA_LITESTREAM_DIR for
# our single ``dinary.db``. Matches the trailing segment of the
# ``path:`` field in .deploy/litestream.yml — a silent drift here
# would make ``inv setup-replica`` restore from the wrong replica tree.
REPLICA_DB_NAME = "dinary"

# Off-site backup to Yandex.Disk (see docs/src/en/operations.md,
# section "Off-site backup: Yandex.Disk"). Everything on the replica
# is managed by ``inv setup-replica``; the restore side is
# ``inv backup-cloud-restore`` (local-only, runs in cwd).
BACKUP_SCRIPT_PATH = "/usr/local/bin/dinary-backup"
BACKUP_RETENTION_SCRIPT_PATH = "/usr/local/bin/dinary-backup-retention"
BACKUP_SERVICE_PATH = "/etc/systemd/system/dinary-backup.service"
BACKUP_TIMER_PATH = "/etc/systemd/system/dinary-backup.timer"

# GFS retention. Closed years are immutable, so ``yearly`` is kept
# indefinitely — every closed-year snapshot is ~300 KB compressed and
# any drift between two yearly snapshots of the same closed year is a
# corruption signal worth retaining forever.
BACKUP_RETENTION_DAILY = 7
BACKUP_RETENTION_WEEKLY = 4
BACKUP_RETENTION_MONTHLY = 12

#: Remote path of the live SQLite ledger file.
_REMOTE_DB_PATH = "/home/ubuntu/dinary/data/dinary.db"

# Re-export so consumers of this module get the full set in one import.
__all__ = [
    "ALLOWED_DOC_LANGUAGES",
    "ALLOWED_VERSION_TYPES",
    "BACKUP_FILENAME_PREFIX",
    "BACKUP_FILENAME_SUFFIX",
    "BACKUP_RETENTION_DAILY",
    "BACKUP_RETENTION_MONTHLY",
    "BACKUP_RETENTION_SCRIPT_PATH",
    "BACKUP_RETENTION_WEEKLY",
    "BACKUP_RCLONE_PATH",
    "BACKUP_RCLONE_REMOTE",
    "BACKUP_SCRIPT_PATH",
    "BACKUP_SERVICE_PATH",
    "BACKUP_STALE_HOURS",
    "BACKUP_TIMER_PATH",
    "CLOUDFLARED_SERVICE",
    "DINARY_SERVICE",
    "LITESTREAM_SERVICE",
    "LITESTREAM_VERSION",
    "LOCAL_ENV_EXAMPLE_PATH",
    "LOCAL_ENV_PATH",
    "LOCAL_IMPORT_SOURCES_PATH",
    "LOCAL_LITESTREAM_CONFIG_PATH",
    "LOCAL_LITESTREAM_EXAMPLE_PATH",
    "REMOTE_DEPLOY_DIR",
    "REMOTE_ENV_PATH",
    "REMOTE_IMPORT_SOURCES_PATH",
    "REMOTE_LEGACY_ENV_PATH",
    "REMOTE_LITESTREAM_CONFIG_PATH",
    "REPLICA_DB_NAME",
    "REPLICA_LITESTREAM_DIR",
    "REPO_URL",
    "VALID_TUNNELS",
    "_REMOTE_DB_PATH",
    "extract_format_flags",
    "extract_year_month",
    "get_allowed_doc_languages",
]
