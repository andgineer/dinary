# VM1 security audit and hardening plan

Items marked **[REGRESSION]** were fixed on the old VM but are
outstanding again on the new one.

## Scope

- Target: VM1, the single production server running the `dinary`
  FastAPI app on SQLite, reachable externally only through Tailscale.
- Out of scope (for now): VM2 (Litestream replica). The same hardening
  should apply there when it is provisioned.

## Current posture — what is correct as of 2026-04-26

- **SSH auth policy**
  - `PasswordAuthentication no`, `KbdInteractiveAuthentication no`,
    `PubkeyAuthentication yes` (via `/etc/ssh/sshd_config.d/`).
  - No password hashes for any account.

- **Network perimeter**
  - Public exposure limited to TCP/22 (SSH) and UDP/41641 (Tailscale
    WireGuard).
  - `iptables INPUT` ends in `REJECT --reject-with icmp-host-prohibited`;
    before it: `ts-input` chain, `RELATED,ESTABLISHED`, `lo`, `icmp`,
    `NEW dport 22`.
  - Oracle Cloud NSG consistent with this.

- **Application exposure**
  - `uvicorn` binds to Tailscale IP only, not
    `0.0.0.0`. Port 8000 unreachable from public internet.
  - `tailscale serve` proxies to `http://127.0.0.1:8000` (tailnet-only, not Funnel).
  - ExecStart uses shell wrapper with `$(tailscale ip -4)` so the bind
    address updates automatically on service restart if the Tailscale
    IP changes.

- **Patching**
  - `unattended-upgrades` active.
  - Kernel: `6.8.0-1047-oracle` (uptime 1 day — freshly provisioned).

- **Sudo**
  - `ubuntu ALL=(ALL) NOPASSWD:ALL` (cloud-init default, only account
    in `sudo` group).

## Findings — by priority

### Critical — `.deploy/.env` is world-readable **[REGRESSION]**

```
-rw-r--r-- 1 root root  /home/ubuntu/dinary/.deploy/.env
```

File contains deploy secrets (host, API keys, sheet logging tokens).
Mode `644` + owned by root means it cannot even be fixed by `ubuntu`
without sudo, and is readable by every process on the box.

Was `600 ubuntu:ubuntu` on the previous VM. Regressed on
re-provisioning because `inv deploy` runs `rsync` as root (via
`subprocess.run(["ssh", ...])`) and does not set the target
permissions.

**Remediation:**

```bash
'sudo chown ubuntu:ubuntu ~/dinary/.deploy/.env && chmod 600 ~/dinary/.deploy/.env'
```

**Productisation:** `inv deploy` (or `sync_remote_env`) should `chmod
600` the file after upload. One-liner fix in `tasks/ssh_utils.py` →
`sync_remote_env`.

### Critical — `dinary.db` and `data/` are world-readable

```
-rw-r--r-- 1 ubuntu ubuntu  ~/dinary/data/dinary.db
-rw-r--r-- 1 ubuntu ubuntu  ~/dinary/data/dinary.db-shm
drwxrwxr-x 2 ubuntu ubuntu  ~/dinary/data/
```

The SQLite database (all financial data) is readable by any local
process. The `data/` directory is also group-writable.

**Remediation:**

```bash
ssh ubuntu@92.4.162.164 '
  chmod 700 ~/dinary/data/
  chmod 600 ~/dinary/data/dinary.db ~/dinary/data/dinary.db-shm ~/dinary/data/dinary.db-wal 2>/dev/null
'
```

**Productisation:** `inv deploy` / `inv restart-server` should ensure
`data/` is `700` and `dinary.db*` are `600` after each start.

### Critical — Google service-account key (status unverified on new VM)

On the old VM:

```
-rw-r--r--  /home/ubuntu/.config/gspread/service_account.json
```

Not checked on the new VM during the 2026-04-26 audit. Must be
verified and fixed before any Google Sheets import is run.

**Remediation:**

```bash
'
  chmod 600 ~/.config/gspread/service_account.json
  chmod 700 ~/.config/gspread
  ls -la ~/.config/gspread/
'
```

### High — root and opc carry the same SSH key **[REGRESSION]**

Oracle cloud-init default. Problems:
1. Laptop key compromise → direct root shell, bypassing sudo audit trail.
2. `opc` is dormant but live entry point.

**Remediation:**

```bash
'
  sudo truncate -s0 /root/.ssh/authorized_keys
  sudo truncate -s0 /home/opc/.ssh/authorized_keys
  sudo usermod -L -s /usr/sbin/nologin opc
  sudo sed -i "s/^#\?PermitRootLogin.*/PermitRootLogin no/" /etc/ssh/sshd_config
  sudo sshd -t && sudo systemctl reload ssh
  sudo sshd -T | grep -E "^(permitrootlogin|passwordauthentication)"
'
```

### Medium — fail2ban not installed **[REGRESSION]**

Was installed and active on the old VM (field-tested: first scanner IP
banned within ~60 s). Not present on the new VM.

**Remediation:** see §"Option 3: fail2ban" below.

### Medium — `X11Forwarding yes` in sshd_config

Not needed for this server. Unnecessary attack surface.

**Remediation:**

```bash
'
  echo "X11Forwarding no" | sudo tee /etc/ssh/sshd_config.d/no-x11.conf
  sudo systemctl reload ssh
'
```

### Medium — `dinary.service` has no systemd sandboxing

The unit runs as `ubuntu` with full default capability bounding set
and none of the `Protect*` / `Restrict*` knobs. For a Python web app
whose only write target is `~/dinary/data/`, this is far more
authority than it needs.

**Remediation** — add to `[Service]` in the unit template
(`tasks/constants.py` `DINARY_SERVICE`), then ship via `inv deploy`:

```ini
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/home/ubuntu/dinary/data
PrivateTmp=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictRealtime=true
RestrictNamespaces=true
LockPersonality=true
MemoryDenyWriteExecute=true
SystemCallArchitectures=native
CapabilityBoundingSet=
AmbientCapabilities=
```

Run the full integration suite after enabling. `MemoryDenyWriteExecute`
occasionally breaks JITs — shouldn't matter for CPython, but verify.

### Low — 3 pending apt upgrades

```
libnetplan0   0.107.1-3ubuntu0.22.04.3
netplan.io    0.107.1-3ubuntu0.22.04.3
snapd         2.74.1+ubuntu22.04.4
```

`unattended-upgrades` is active but hasn't run yet on the fresh VM.
Not critical — no CVE-tagged packages in the list.

### Low — public SSH attracts brute-force scanning

Same as before: password auth is off so no credentialed entry is
possible, but log noise and CPU overhead remain.
See §"Log swelling — how to prevent it" below.

## Log swelling — how to prevent it

### Option 1 (tried, reverted): bind SSH to Tailscale only

All administrative access already goes through Tailscale. Keeping
TCP/22 on the public interface adds zero capability and 100% of
scanner noise.

```bash
'
  sudo tee /etc/ssh/sshd_config.d/10-tailscale-only.conf >/dev/null <<EOF
ListenAddress 100.86.30.123:22
ListenAddress 127.0.0.1:22
EOF
  sudo sshd -t && sudo systemctl reload ssh
  sudo ss -tlnp | grep :22
'
```

**Why this was reverted in April 2026.** The posture was applied to
both VM1 and VM2 and worked at the network level. The problem: VM1's
FastAPI ingress, SSH ingress, VM2's SSH ingress, and the Litestream
SFTP stream between them all depend on the same `tailscaled` process
and Tailscale coordination server. A single control-plane outage, a
`tailscaled` crash, or accidental key-expiry would simultaneously take
out operator access and the PWA data path — with no independent
recovery because the `ubuntu` user is locked (`passwd -l`) on both
hosts, so Oracle Cloud Serial Console cannot authenticate a fallback
shell. Option 3 below is the durable posture.

Break-glass path if Tailscale is unreachable: Oracle Cloud Serial
Console bypasses the network stack.

### Option 2: keep public 22, rate-limit in iptables

```bash
'
  sudo iptables -I INPUT -p tcp --dport 22 -m state --state NEW -m recent --set --name SSH
  sudo iptables -I INPUT -p tcp --dport 22 -m state --state NEW -m recent --update --seconds 60 --hitcount 4 --name SSH -j DROP
  sudo netfilter-persistent save 2>/dev/null \
    || sudo iptables-save | sudo tee /etc/iptables/rules.v4 >/dev/null
'
```

### Option 3 (was applied on old VM, needs re-applying): fail2ban

```bash
'
  sudo apt-get install -y fail2ban
  sudo tee /etc/fail2ban/jail.local >/dev/null <<EOF
[DEFAULT]
ignoreip = 127.0.0.1/8 ::1 100.64.0.0/10
bantime = 1d
bantime.increment = true
bantime.factor = 2
bantime.maxtime = 30d
findtime = 10m
maxretry = 3

[sshd]
enabled = true
backend = systemd
EOF
  sudo systemctl enable --now fail2ban
  sudo fail2ban-client status sshd
'
```

Apply on VM2 in parallel with the same `jail.local`.

### Option 4 (defense in depth): cap journald retention

```bash
'
  sudo mkdir -p /etc/systemd/journald.conf.d
  sudo tee /etc/systemd/journald.conf.d/retention.conf >/dev/null <<EOF
[Journal]
SystemMaxUse=200M
SystemKeepFree=500M
MaxRetentionSec=30day
EOF
  sudo systemctl restart systemd-journald
  journalctl --disk-usage
'
```

## Recommended remediation order

1. **Today — Critical**: `chmod 600` `.deploy/.env` + fix `data/`
   permissions + verify Google service-account key.
2. **Today — High**: wipe root/opc `authorized_keys`, lock `opc`,
   `PermitRootLogin no`.
3. **Today — Medium**: disable `X11Forwarding`, reinstall `fail2ban`
   (regression from old VM).
4. **This week — Low**: apply pending apt upgrades.
5. **Next deploy cycle — Medium**: add systemd sandboxing block to
   `dinary.service` unit template in `tasks/constants.py`, ship via
   `inv deploy`.

## Productising the fixes

- `sync_remote_env` in `tasks/ssh_utils.py` → `chmod 600` after upload.
- `inv deploy` / `inv restart-server` → ensure `data/` is `700`,
  `dinary.db*` are `600`.
- `inv setup-server` / `inv setup-replica` → include `fail2ban`
  install + `jail.local`, `X11Forwarding no` drop-in, root/opc key
  wipe, `PermitRootLogin no`. This ensures a fresh VM starts hardened
  without a separate audit run.
