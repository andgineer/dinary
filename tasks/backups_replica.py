"""VM2 (Litestream replica) bootstrap and resync tasks.

Three layers, all routed through this module:

* Pure script builders (``_build_setup_replica_*``) — emit the bash
  blobs that VM2 ultimately runs. Tests pin their observable contract.
* :func:`setup_replica` — the orchestrator that wires the four
  bootstrap steps (packages, dir, swap, ssh hardening) and configures
  VM1's Litestream replicator to push to VM2.
* :func:`replica_resync` — post-restore housekeeping that resets the
  replica's WAL position by deleting its stale DB copy and restarting
  litestream so it pulls a fresh restore.
"""

import sys
from pathlib import Path

from invoke import task

from .constants import (
    LITESTREAM_SERVICE,
    LOCAL_LITESTREAM_CONFIG_PATH,
    LOCAL_LITESTREAM_EXAMPLE_PATH,
    REMOTE_LITESTREAM_CONFIG_PATH,
    REPLICA_LITESTREAM_DIR,
)
from .env import replica_host
from .ssh_utils import (
    build_setup_swap_script,
    build_ssh_tailscale_only_script,
    create_service,
    litestream_install_script,
    ssh_replica,
    ssh_run,
    ssh_sudo,
    write_remote_file,
)


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
