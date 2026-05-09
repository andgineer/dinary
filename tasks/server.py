"""Server-facing tasks: status, logs, restart, SSH sessions."""

from invoke import task

from .constants import REMOTE_LITESTREAM_CONFIG_PATH
from .env import host, replica_host, tunnel
from .ssh_utils import (
    ssh_run,
    ssh_sudo,
)


@task(name="restart-server")
def restart_server(c):
    """Restart the dinary systemd service on the server."""
    ssh_sudo(c, "systemctl restart dinary")
    print("=== Restarted. Checking health... ===")
    ssh_run(c, "sleep 5 && curl -s http://localhost:8000/api/health")


@task
def logs(c, follow=False, lines=100, remote=False):
    """Show dinary service logs.

    Flags:
        --remote      Fetch logs from the production server over SSH.
                      Default runs locally (prints a hint since local dev
                      logs appear in the ``inv dev`` terminal).
        -f            Follow log output (remote only).
        -l N          Number of lines to show (default 100, remote only).
    """
    if not remote:
        print("Local dev logs appear in the terminal when running `inv dev`.")
        return
    flag = "-f" if follow else f"-n {lines} --no-pager"
    c.run(f"ssh {host()} 'sudo journalctl -u dinary {flag}'")


@task
def status(c, remote=False):
    """Show dinary service status and Litestream replicator state.

    Flags:
        --remote   Check the production server over SSH.
                   Default checks local dev server at localhost:8000.
    """
    if not remote:
        c.run("curl -sf http://localhost:8000/api/health || echo 'Server not responding'")
        return
    tun = tunnel()
    ssh_sudo(c, "systemctl status dinary --no-pager")
    if tun == "tailscale":
        ssh_run(c, "tailscale serve status")
    elif tun == "cloudflare":
        ssh_sudo(c, "systemctl status cloudflared --no-pager")
    ssh_run(c, "systemctl status litestream --no-pager || true")
    ssh_run(c, "sudo journalctl -u litestream -n 30 --no-pager || true")
    ssh_run(c, f"litestream databases -config {REMOTE_LITESTREAM_CONFIG_PATH} || true")


@task
def ssh(c):
    """Open SSH session to the server."""
    c.run(f"ssh {host()}", pty=True)


@task(name="ssh-replica")
def ssh_replica(c):
    """Open SSH session to the replica (VM2)."""
    c.run(f"ssh {replica_host()}", pty=True)
