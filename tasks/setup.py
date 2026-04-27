"""VM setup tasks: setup-server."""

from invoke import task

from .constants import (
    DINARY_SERVICE,
    REPO_URL,
)
from .env import bind_host, host, tunnel
from .ssh_utils import (
    build_data_dir_permissions_script,
    build_harden_sshd_script,
    build_install_fail2ban_script,
    build_setup_swap_script,
    build_ssh_tailscale_only_script,
    create_service,
    setup_cloudflare,
    setup_tailscale,
    ssh_run,
    ssh_sudo,
    sync_remote_env,
    sync_remote_import_sources,
)


@task(name="setup-server")
def setup_server(c, no_swap=False, tailscale=False):  # noqa: PLR0915
    """One-time VM1 setup: install deps, clone repo, create services, upload creds.

    To seed categories to the DB call `inv bootstrap-catalog --yes`.

    Flags:
        --no-swap          Skip swap provisioning (already allocated on re-run).
        --tailscale        After joining the tailnet (requires
            ``DINARY_TUNNEL=tailscale``), rebind ``sshd`` to the
            Tailscale IP + loopback only, closing public TCP/22.
            Off by default so a first-time operator is never locked out.
            Break-glass if locked out: Oracle Cloud → VM Instance page →
            Console connection → Launch Cloud Shell, then delete
            ``/etc/ssh/sshd_config.d/10-tailscale-only.conf`` and
            ``systemctl reload ssh``.
    """
    setup_host = host()
    setup_tunnel = tunnel()

    print("=== Hardening: disable rpcbind, verify iptables ===")
    ssh_run(
        c,
        "sudo systemctl stop rpcbind rpcbind.socket 2>/dev/null; "
        "sudo systemctl disable rpcbind rpcbind.socket 2>/dev/null; "
        "sudo iptables -C INPUT -i lo -j ACCEPT 2>/dev/null || "
        "sudo iptables -I INPUT 3 -i lo -j ACCEPT; "
        "sudo iptables -C INPUT -j REJECT --reject-with icmp-host-prohibited 2>/dev/null || "
        "sudo iptables -A INPUT -j REJECT --reject-with icmp-host-prohibited; "
        "sudo netfilter-persistent save 2>/dev/null; "
        "true",
    )

    print("=== Hardening: sshd (X11 off, PermitRootLogin no, wipe root/opc keys) ===")
    ssh_run(c, build_harden_sshd_script())

    print("=== Installing system packages ===")
    ssh_sudo(
        c,
        "apt update && sudo apt install -y python3 python3-pip git curl sqlite3 rclone",
    )

    print("=== Installing fail2ban ===")
    ssh_run(c, build_install_fail2ban_script())

    if not no_swap:
        print("=== Provisioning swap file ===")
        ssh_run(c, build_setup_swap_script(size_gb=1))

    print("=== Installing uv ===")
    ssh_run(c, "curl -LsSf https://astral.sh/uv/install.sh | sh")

    print("=== Cloning repo ===")
    ssh_run(c, f"test -d ~/dinary || git clone {REPO_URL} ~/dinary")
    ssh_run(c, "cd ~/dinary && git pull && source ~/.local/bin/env && uv sync --no-dev")

    print("=== Ensuring data/ directory (0700) ===")
    ssh_run(c, "mkdir -p ~/dinary/data && " + build_data_dir_permissions_script())

    print("=== Syncing .deploy/.env to server ===")
    sync_remote_env(c)

    print("=== Syncing .deploy/import_sources.json to server (if present) ===")
    sync_remote_import_sources(c)

    print("=== Uploading credentials ===")
    ssh_run(c, "mkdir -p ~/.config/gspread && chmod 700 ~/.config/gspread")
    c.run(
        f"scp ~/.config/gspread/service_account.json "
        f"{setup_host}:~/.config/gspread/service_account.json",
    )
    ssh_run(c, "chmod 600 ~/.config/gspread/service_account.json")

    bh = bind_host(setup_tunnel)
    print(f"=== Creating dinary service (bind {bh}) ===")
    service = DINARY_SERVICE.format(host=bh)
    create_service(c, "dinary", service)

    if setup_tunnel == "tailscale":
        setup_tailscale(c)
        if tailscale:
            print("=== Restricting sshd to Tailscale + loopback ===")
            ssh_run(c, build_ssh_tailscale_only_script())
    elif setup_tunnel == "cloudflare":
        setup_cloudflare(c)
        if tailscale:
            msg = (
                "--tailscale requires DINARY_TUNNEL=tailscale "
                "(the flag rebinds sshd to the tailscaled IPv4). "
                "Either set DINARY_TUNNEL=tailscale or drop the flag."
            )
            raise RuntimeError(msg)
    else:
        print("=== No tunnel configured (DINARY_TUNNEL=none) ===")
        if tailscale:
            msg = "--tailscale requires DINARY_TUNNEL=tailscale; current value is ``none``."
            raise RuntimeError(msg)

    print("=== Done! Checking health... ===")
    ssh_run(c, "sleep 15 && curl -s http://localhost:8000/api/health")
