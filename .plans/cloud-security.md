# VM1 security audit and hardening plan

Snapshot as of 2026-04-22 of VM1 (Oracle Cloud, public IP
`130.110.19.227`, hostname `dinary`), plus concrete remediations. All
commands below are idempotent and safe to replay.

## Scope

- Target: VM1, the single production server running the `dinary`
  FastAPI app on SQLite, reachable externally only through Tailscale
  Serve.
- Out of scope (for now): VM2, which will host the Litestream SFTP
  replica per `.plans/storage-migration.md`. The same hardening should
  apply there when it is provisioned.

## Current posture — what is already correct

- **SSH auth policy**
  - `PasswordAuthentication no`, `ChallengeResponseAuthentication no`,
    `KbdInteractiveAuthentication no`, `PubkeyAuthentication yes`.
  - No password hashes set in `/etc/shadow` for any account — all
    entries are locked (`!` / `*`).
  - Modern ciphers and KEX only: chacha20-poly1305, aes-gcm, aes-ctr;
    curve25519, ecdh-nistp*, sntrup761x25519.

- **Network perimeter**
  - Public exposure limited to TCP/22 (SSH). TCP/80 and TCP/8000 are
    unreachable from outside (verified with an external `curl` → hard
    timeout).
  - `iptables INPUT` chain ends in a `REJECT --reject-with
    icmp-host-prohibited`; before it, only the Tailscale chain
    (`ts-input`), `RELATED,ESTABLISHED`, `lo`, `icmp`, and `NEW dport
    22` are accepted.
  - Oracle Cloud NSG appears to be consistent with this (nothing
    reaches the VM NIC beyond 22 from the public side).

- **Application exposure**
  - `uvicorn` binds **only** to `127.0.0.1:8000`. No public HTTP
    surface.
  - External access is exclusively via `tailscale serve` at
    `https://dinary.tail1e48d.ts.net`, which is tailnet-only (not
    Tailscale Funnel). Confirmed in `tailscale serve status`.

- **Patching**
  - `unattended-upgrades` is `enabled` + `active`. Last runs: today,
    multiple invocations by the systemd timer.
  - Kernel: `6.8.0-1044-oracle`.

- **AppArmor** — loaded, 29 profiles in use.

- **Sudo**
  - Group `sudo` contains only `ubuntu`.
  - `/etc/sudoers.d/` has only system-managed files (`cloud-init`,
    `oracle-cloud-agent`); no ad-hoc rules.

- **Application secrets on disk**
  - `/home/ubuntu/dinary/.deploy/.env` is mode `600`, owner
    `ubuntu:ubuntu`.
  - `/home/ubuntu/dinary/.deploy/import_sources.json` is mode `600`.

- **SSH probe traffic is harmless** — scanners from the internet probe
  accounts like `admin`, `postgres`, `test`, `ftpuser`, `pedro`. None
  exist, and password auth is off, so credentialed entry is
  impossible. The noise pollutes logs but does not create a foothold.

## Findings — by priority

### Critical — Google service-account key is world-readable

```
-rw-r--r--  /home/ubuntu/.config/gspread/service_account.json
```

That file is a **Google API private key** used by `gspread` to
authenticate against Sheets/Drive. Mode `644` means any local process
under any UID can read it. The only other human UID on the box is
`opc` (Oracle default, otherwise unused), but file permissions should
enforce the invariant regardless.

**Remediation** (apply once, idempotent):

```bash
ssh ubuntu@130.110.19.227 '
  chmod 600 ~/.config/gspread/service_account.json
  chmod 700 ~/.config/gspread
  ls -la ~/.config/gspread/
'
```

### High — root and opc carry the same SSH key, and `PermitRootLogin without-password`

```
-- ubuntu --  SHA256:Ia/q2L9xgevez9jj81VAg1smct1dC3gMijgtEuHKtjQ andgineer@ya.ru
-- root   --  SHA256:Ia/q2L9xgevez9jj81VAg1smct1dC3gMijgtEuHKtjQ andgineer@ya.ru
-- opc    --  SHA256:Ia/q2L9xgevez9jj81VAg1smct1dC3gMijgtEuHKtjQ andgineer@ya.ru
```

All three accounts accept the same laptop key. This is the cloud-init
default on Oracle Ubuntu images. The practical problems:

1. **Sudo audit bypass**: a compromise of the laptop key grants `root`
   directly, skipping `ubuntu`+`sudo` and the log trail that comes
   with it.
2. **Forensics gap**: a `root` SSH session is not bound to a human
   `last`/`wtmp` record in the same way a `ubuntu → sudo -i` session
   is.
3. `opc` is a dormant, unused account that is nevertheless a live
   entry point.

**Remediation**:

```bash
ssh ubuntu@130.110.19.227 '
  sudo truncate -s0 /root/.ssh/authorized_keys
  sudo truncate -s0 /home/opc/.ssh/authorized_keys
  sudo usermod -L -s /usr/sbin/nologin opc
  sudo sed -i "s/^#\\?PermitRootLogin.*/PermitRootLogin no/" /etc/ssh/sshd_config
  sudo sshd -t && sudo systemctl reload ssh
  sudo sshd -T | grep -E "^(permitrootlogin|passwordauthentication)"
'
```

After this, only `ubuntu` can SSH in; root is reached strictly through
`sudo`.

### Medium — kernel reboot pending

```
/var/run/reboot-required
14 upgradable packages
uptime: 7 days
```

`unattended-upgrades` has staged kernel and library updates on disk,
but they are not active until reboot. The running kernel is carrying
un-patched code paths.

**Remediation** — schedule a reboot at a quiet hour. `dinary.service`
is declared `enabled` and will come back up through systemd:

```bash
ssh ubuntu@130.110.19.227 'sudo shutdown -r +1 "dinary maintenance reboot"'
```

### Medium — `dinary.service` has no systemd sandboxing

```
NoNewPrivileges=no
ProtectSystem=no
ProtectHome=no
PrivateTmp=no
ProtectKernelTunables=no
ProtectKernelModules=no
ProtectKernelLogs=no
RestrictSUIDSGID=no
CapabilityBoundingSet=<full root-capable set, 40 capabilities>
```

The unit runs as `ubuntu` but keeps the full default capability
bounding set and none of the `Protect*` / `Restrict*` knobs. For a
Python web app whose only write target is `/home/ubuntu/dinary/data/`
and whose only network target is outbound HTTPS, this is far more
authority than it needs.

**Remediation** — add the hardening block below to
`/etc/systemd/system/dinary.service` under `[Service]` (or to whatever
unit file ships via `inv deploy`), then `systemctl daemon-reload &&
systemctl restart dinary` and verify with `systemctl show dinary |
grep Protect`:

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

### Low — public SSH attracts continuous brute-force scanning

- No credentialed entry is possible (password auth off, scanners pick
  wrong usernames), so this is purely a **log volume** and **CPU on
  handshake** issue.
- Log rate today: a few dozen `Invalid user ...` lines per hour.
- `fail2ban` is not installed.

See §"Log swelling — how to prevent it" below for the recommended
shape of the fix.

### Low — repo litter with surface read-anybody bits

```
-rw-rw-r-- /home/ubuntu/dinary/.deploy.example/.env
-rw-rw-r-- /home/ubuntu/dinary/.deploy.example/import_sources.json
-rw-r--r-- /home/ubuntu/dinary/._package.json
-rw-r--r-- /home/ubuntu/dinary/._manifest.json
-rw-r--r-- /home/ubuntu/dinary/._.env
```

- `.deploy.example/` is a **template**, not a secret, but the bits
  should still be tightened to `640` for hygiene.
- `._*` files are macOS AppleDouble metadata files that leaked in via
  `scp` / `rsync` from a Mac. They are not secrets, but they do
  confuse diff / lint tooling.

**Remediation**:

```bash
ssh ubuntu@130.110.19.227 '
  chmod 640 /home/ubuntu/dinary/.deploy.example/*
  find /home/ubuntu/dinary -xdev -name "._*" -delete
'
```

Add `._*` to `.gitignore` locally to stop the churn at the source.

## Log swelling — how to prevent it

The SSH brute-force noise today is the main log source; the choice is
between eliminating the source and just capping the damage. Listed in
descending order of impact.

### Option 1 (recommended): bind SSH to Tailscale only

All administrative access already goes through Tailscale; the laptop
is in the tailnet. Keeping TCP/22 open on the public interface adds
zero capability and 100% of the scanner noise.

```bash
ssh ubuntu@130.110.19.227 '
  sudo tee /etc/ssh/sshd_config.d/10-tailscale-only.conf >/dev/null <<EOF
ListenAddress 100.110.4.119:22
ListenAddress 127.0.0.1:22
EOF
  sudo sshd -t && sudo systemctl reload ssh
  sudo ss -tlnp | grep :22
'
```

After reload, `0.0.0.0:22` disappears from `ss`. Verify you can still
reach the box via the tailnet name (`ssh ubuntu@dinary`) **from a
different terminal** before closing the current session.

Break-glass path if Tailscale is ever unreachable: Oracle Cloud
console exposes a Serial Console that bypasses the network stack
entirely, so losing public TCP/22 is safe.

### Option 2: keep public 22, rate-limit in iptables

Single pair of rules, no daemon, no memory overhead. Drops a source IP
that opens 4+ new connections within 60 seconds:

```bash
ssh ubuntu@130.110.19.227 '
  sudo iptables -I INPUT -p tcp --dport 22 -m state --state NEW -m recent --set --name SSH
  sudo iptables -I INPUT -p tcp --dport 22 -m state --state NEW -m recent --update --seconds 60 --hitcount 4 --name SSH -j DROP
  sudo netfilter-persistent save 2>/dev/null \
    || sudo iptables-save | sudo tee /etc/iptables/rules.v4 >/dev/null
'
```

Packets are `DROP`ped before `sshd` sees them, so those attempts never
generate log lines. Typical scanners try once or twice and move on →
~90% noise reduction.

### Option 3: fail2ban

Reads `auth.log`, bans offending IPs through a dynamic iptables chain.
First 2–3 lines per scanner are still written, but the same IP cannot
retry for `bantime`:

```bash
ssh ubuntu@130.110.19.227 '
  sudo apt-get install -y fail2ban
  sudo tee /etc/fail2ban/jail.d/sshd.local >/dev/null <<EOF
[sshd]
enabled = true
maxretry = 3
bantime  = 1h
findtime = 10m
EOF
  sudo systemctl enable --now fail2ban
  sudo fail2ban-client status sshd
'
```

Upside: nice `fail2ban-client status` view. Downside: one more moving
part, one more Python3 daemon pinned in RAM, and logs still grow a
little.

### Option 4 (defense in depth, orthogonal): cap journald retention

Even with Options 1/2/3 in place, cap the worst case so `/` can never
be filled by log spam from any source:

```bash
ssh ubuntu@130.110.19.227 '
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

Current usage is `112M`, so `200M` headroom absorbs seasonal spikes
without impacting steady state.

## Recommended remediation order

1. **Today**: §Critical — `chmod 600` the Google service-account key.
2. **Today**: §High — wipe root/opc `authorized_keys`, lock `opc`,
   `PermitRootLogin no`.
3. **This week**: §Medium (log volume) — Option 1 (Tailscale-only
   SSH) + Option 4 (journald caps).
4. **This week**: §Medium (kernel reboot).
5. **Next deploy cycle**: §Medium — add the sandboxing block to
   `dinary.service` and ship it via `inv deploy`.
6. **Cleanup, no urgency**: §Low — repo litter and `.deploy.example`
   permissions.

## Productising the fixes

Where the remediation is a one-liner on a running host (service
account chmod, SSH Tailscale-only bind, journald retention, root key
cleanup) it should become an idempotent `inv` task — same pattern as
`inv setup-swap`:

- `inv secure-google-creds` — `chmod 600` the service account JSON.
- `inv ssh-lockdown` — wipe root/opc keys, lock opc, set
  `PermitRootLogin no`, bind sshd to Tailscale + loopback.
- `inv journald-caps` — drop the retention config.
- Updates to the existing unit template in `.deploy/dinary.service`
  (or wherever `inv deploy` renders it) — ship the sandboxing block.

Wiring them into `inv setup` means VM2, when it is provisioned for
Litestream, inherits the same posture automatically and we don't
repeat this audit.
