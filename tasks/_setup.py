"""VM setup tasks: setup, setup-swap, ssh-tailscale-only."""

from pathlib import Path

from invoke import task

from ._common import (
    DINARY_SERVICE,
    LOCAL_IMPORT_SOURCES_PATH,
    LOCAL_LITESTREAM_CONFIG_PATH,
    REPO_URL,
    _bind_host,
    _build_setup_swap_script,
    _build_ssh_tailscale_only_script,
    _create_service,
    _host,
    _setup_cloudflare,
    _setup_tailscale,
    _ssh,
    _ssh_sudo,
    _sync_remote_env,
    _sync_remote_import_sources,
    _tunnel,
)
from ._deploy import bootstrap_catalog, import_config


@task(name="ssh-tailscale-only")
def ssh_tailscale_only(c):
    """Rebind ``sshd`` to Tailscale + loopback, closing public TCP/22.

    Writes ``/etc/ssh/sshd_config.d/10-tailscale-only.conf`` with
    ``ListenAddress <tailscale-ipv4>:22`` and
    ``ListenAddress 127.0.0.1:22``, validates the merged config with
    ``sshd -t``, and reloads ``ssh.service``. The Tailscale IPv4 is
    read off the remote itself (``tailscale ip -4``) so replays after
    a Tailscale IP rotation converge without a local re-run of
    ``inv setup``.

    Pre-conditions (enforced remotely; the task aborts if violated):

    * ``tailscale`` command is installed;
    * ``tailscaled`` is up and logged in (``tailscale ip -4`` returns
      a non-empty IPv4).

    Operator pre-flight:

    1. Confirm the current shell still has an open session.
    2. From a **second** terminal, verify ``ssh <tailnet-name>`` works
       *before* running this — e.g. ``ssh ubuntu@dinary hostname``.
    3. Only then run ``inv ssh-tailscale-only``.

    Break-glass if locked out regardless: Oracle Cloud VM Instance
    page → "Console connection" → "Launch Cloud Shell connection"
    attaches a serial console that bypasses the network stack. Delete
    ``/etc/ssh/sshd_config.d/10-tailscale-only.conf`` and
    ``systemctl reload ssh``.
    """
    _ssh(c, _build_ssh_tailscale_only_script())


@task(name="setup-swap")
def setup_swap(c, size_gb=1):
    """Provision a persistent ``/swapfile`` on the server (idempotent).

    Oracle Cloud Always Free VMs ship with zero swap and ~1 GiB RAM,
    so a transient memory spike (bulk import, ``uv sync`` on a fat
    lockfile, a cloudflared update) can OOM-kill ``dinary`` even
    with 40 GB of idle disk. This task allocates ``/swapfile``,
    activates it, and wires it into ``/etc/fstab`` so the swap
    survives reboots.

    Re-running is safe: if ``/swapfile`` is already active the
    allocation is skipped, and the fstab line is appended only when
    not already present. Changing ``--size-gb`` on a re-run does
    **not** silently resize — operators must ``swapoff /swapfile``
    and ``rm /swapfile`` manually first, otherwise the system would
    briefly go to zero swap under load during the resize.

    Flags:
        --size-gb N   swap file size in gigabytes (default 1).
    """
    size = int(size_gb)
    script = _build_setup_swap_script(size_gb=size)
    _ssh(c, script)


@task
def setup(c, lock_ssh_to_tailnet=False):  # noqa: PLR0915
    """One-time VM setup: install deps, clone repo, create services, upload creds.

    Flags:
        --lock-ssh-to-tailnet  After Tailscale is joined (requires
            ``DINARY_TUNNEL=tailscale``), rebind ``sshd`` to the
            Tailscale IP + loopback only, closing public TCP/22.
            Delegates to ``inv ssh-tailscale-only``; see that task for
            safety pre-conditions and the break-glass path. Off by
            default so a first-time operator is never locked out.
    """
    host = _host()
    tunnel = _tunnel()

    print("=== Hardening: disable rpcbind, verify iptables ===")
    _ssh(
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

    print("=== Installing system packages ===")
    _ssh_sudo(
        c,
        "apt update && sudo apt install -y python3 python3-pip git curl sqlite3 rclone",
    )

    print("=== Provisioning swap file ===")
    setup_swap(c)

    print("=== Installing uv ===")
    _ssh(c, "curl -LsSf https://astral.sh/uv/install.sh | sh")

    print("=== Cloning repo ===")
    _ssh(c, f"test -d ~/dinary || git clone {REPO_URL} ~/dinary")
    _ssh(c, "cd ~/dinary && git pull && source ~/.local/bin/env && uv sync --no-dev")

    print("=== Ensuring data/ directory ===")
    _ssh(c, "mkdir -p ~/dinary/data")

    print("=== Syncing .deploy/.env to server ===")
    _sync_remote_env(c)

    print("=== Syncing .deploy/import_sources.json to server (if present) ===")
    _sync_remote_import_sources(c)

    print("=== Uploading credentials ===")
    _ssh(c, "mkdir -p ~/.config/gspread")
    c.run(
        f"scp ~/.config/gspread/service_account.json {host}:~/.config/gspread/service_account.json",
    )

    bind_host = _bind_host(tunnel)
    print(f"=== Creating dinary service (bind {bind_host}) ===")
    service = DINARY_SERVICE.format(host=bind_host)
    _create_service(c, "dinary", service)

    print("=== Bootstrapping runtime catalog (no Google Sheets required) ===")
    bootstrap_catalog(c)

    if Path(LOCAL_IMPORT_SOURCES_PATH).exists():
        print("=== Importing catalog from Google Sheets (import_sources.json present) ===")
        import_config(c)
    else:
        print(
            "=== Skipping import-config (no .deploy/import_sources.json locally). ===\n"
            "=== Runtime catalog is populated; /api/expenses will work. ===",
        )

    if tunnel == "tailscale":
        _setup_tailscale(c)
        if lock_ssh_to_tailnet:
            print("=== Restricting sshd to Tailscale + loopback ===")
            ssh_tailscale_only(c)
    elif tunnel == "cloudflare":
        _setup_cloudflare(c)
        if lock_ssh_to_tailnet:
            msg = (
                "--lock-ssh-to-tailnet requires DINARY_TUNNEL=tailscale "
                "(the flag rebinds sshd to the tailscaled IPv4, which "
                "is only guaranteed to exist after ``inv setup`` joined "
                "the tailnet). Either set DINARY_TUNNEL=tailscale or drop "
                "the flag."
            )
            raise RuntimeError(msg)
    else:
        print("=== No tunnel configured (DINARY_TUNNEL=none) ===")
        if lock_ssh_to_tailnet:
            msg = (
                "--lock-ssh-to-tailnet requires DINARY_TUNNEL=tailscale; "
                "current value is ``none``. See `inv ssh-tailscale-only` "
                "for the standalone form once Tailscale is configured."
            )
            raise RuntimeError(msg)

    if Path(LOCAL_LITESTREAM_CONFIG_PATH).exists():
        print(
            "=== .deploy/litestream.yml present — run `inv litestream-setup` ===\n"
            "=== manually once the SFTP replica host trusts VM 1's ssh key. ===",
        )
    else:
        print(
            "=== Skipping Litestream (no .deploy/litestream.yml locally). ===\n"
            "=== Copy .deploy.example/litestream.yml and run `inv litestream-setup` "
            "when you have an SFTP replica target. ===",
        )

    print("=== Done! Checking health... ===")
    _ssh(c, "sleep 15 && curl -s http://localhost:8000/api/health")
