# dinary-ai — implementation plan

Background service: MCP server + live DB replica from VM1 via Litestream.
`inv analytics` detects whether `dinary-ai` is running and auto-configures it via `setup-ai-replica`.

---

## Architecture summary

- VM1 Litestream pushes WAL (LTX files) to the machine via Tailscale SFTP, alongside the existing VM2 replica.
- `dinary-ai` serves FastMCP on HTTP and keeps a local SQLite replica fresh via periodic `litestream restore` from local LTX files.
- On first start (no local LTX files): SSH restore from VM2 for immediate availability, same as `_sync_replica()`.
- On startup: `dinary-ai` self-registers on VM1 (adds itself to Litestream config, removes stale entries, restarts Litestream if changed, verifies via journalctl).
- `inv analytics` checks if `dinary-ai` is reachable; if not, runs `setup-ai-replica` idempotently before opening Marimo.
- Cross-platform: macOS (launchd) and Windows (Task Scheduler).

---

## Step 1 — Remove MCP server from `inv analytics`

**File**: `tasks/analytics.py`

Remove: `subprocess`, `mcp_proc`, `_DEFAULT_MCP_PORT`, try/finally MCP start/stop block.

Add before opening Marimo:
```python
def _ensure_dinary_ai(c, mcp_port: int) -> None:
    """Start dinary-ai via setup-ai-replica if not reachable on mcp_port."""
    import urllib.request, urllib.error
    try:
        urllib.request.urlopen(f"http://localhost:{mcp_port}/mcp", timeout=2)
        print(f"OK: dinary-ai reachable on port {mcp_port}")
    except (urllib.error.URLError, OSError):
        print(f"dinary-ai not running on port {mcp_port} — running setup-dinary-ai")
        c.run("uv run inv setup-dinary-ai", pty=True)
```

`analytics` task signature loses `--mcp-port`; keeps `--port` for Marimo only.

---

## Step 2 — Extend Litestream config to multiple replicas

**File**: `tasks/backups/backups_replica.py` → `_build_litestream_config()`

Change `replica:` (single) → `replicas:` (list). VM2 is always first, permanent.
Accept optional `ai_replicas: list[dict]` parameter (default empty list).

```yaml
dbs:
  - path: /path/to/dinary.db
    replicas:
      - type: sftp          # VM2 — permanent, always first
        host: vm2host:22
        ...
      - type: sftp          # AI replica — dynamic, 0..N entries
        host: machine.tailnet:22
        user: andrei
        key-path: /etc/litestream/ai_key
        path: /home/andrei/.local/share/dinary/litestream/dinary.db
```

Validation before writing: assert VM2 entry is present, else raise — never write config without VM2.

Update `setup_replica` task: passes empty `ai_replicas=[]` (preserves existing behaviour).

Tests in `tests/tasks/test_tasks_backups_replica.py`:
- `test_multi_replica_includes_vm2` — VM2 always present alongside AI replicas
- `test_refuses_config_without_vm2` — raises if VM2 entry missing after build

---

## Step 3 — AI replica registry on VM1

Directory on VM1: `/var/lib/dinary-replicas/` (add creation to `setup_replica` task, idempotent `mkdir -p`).

Each active machine writes `<hostname>.json`:
```json
{"host": "macbook.tailnet", "sftp_path": "/Users/andrei/.local/share/dinary/litestream/dinary.db", "user": "andrei", "last_seen": "2026-06-06T10:00:00Z"}
```

Stale threshold: `DINARY_AI_REPLICA_TTL_DAYS` env var, default 30. Constant in `tasks/devtools/constants.py`.

---

## Step 4 — `src/dinary_analytics/ai_replica.py`

Pure functions (testable without SSH):

- `build_ai_replica_entry(hostname, tailscale_host, sftp_path, user) -> dict`
- `is_stale(entry: dict, now: datetime, ttl_days: int) -> bool`
- `ai_entries_to_litestream_replicas(entries: list[dict]) -> list[dict]`

SSH operations:

- `read_ai_registry(ssh_fn) -> list[dict]` — reads all `*.json` from `/var/lib/dinary-replicas/` on VM1
- `write_ai_entry(ssh_fn, entry: dict)` — writes `<hostname>.json` on VM1
- `remove_ai_entry(ssh_fn, hostname: str)` — removes stale entry on VM1
- `rebuild_vm1_litestream_config(ssh_fn, ai_entries: list[dict])` — calls `_build_litestream_config(ai_replicas=...)`, writes `/etc/litestream.yml`, validates VM2 present
- `restart_litestream_and_verify(ssh_fn) -> None` — restarts litestream on VM1, waits 3 s, reads journalctl, raises if error lines present (reuses `_parse_litestream_errors`)

Tests in `tests/analytics/test_ai_replica.py`:
- `test_is_stale_*` — boundary cases for TTL
- `test_ai_entries_to_litestream_replicas` — correct YAML structure
- `test_build_ai_replica_entry` — fields present

---

## Step 5 — SSH key exchange during registration

VM1 pushes to machine via SFTP → VM1 needs a keypair that machine's SFTP trusts.

During `dinary-ai` startup:
1. SSH to VM1, run `build_ensure_vm1_replica_key_script()` (already exists) → get VM1 Litestream pubkey.
2. Append to `~/.ssh/authorized_keys` locally (local file write).
3. Register machine in VM1 registry with Tailscale IP + SFTP path.

Machine SFTP:
- macOS: `sudo systemsetup -setremotelogin on` — `dinary-ai` checks and enables on first run.
- Windows: `Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0` — `dinary-ai` checks; prints instructions if not enabled (requires admin elevation, cannot auto-enable).

---

## Step 6 — Local LTX directory and restore

Platform paths (in `dinary_analytics/paths.py`):
- macOS: `~/.local/share/dinary/litestream/dinary.db/`
- Windows: `%LOCALAPPDATA%/dinary/litestream/dinary.db/`

`restore_local_replica() -> Path`:
- Writes temp litestream config (`type: file`, local LTX path)
- Runs `litestream restore`
- Raises `LitestreamRestoreError` if no valid snapshot

Fallback: `LitestreamRestoreError` → `_sync_replica()` (SSH restore from VM2). Logs warning.

Litestream binary check: `shutil.which("litestream")` at startup; if missing, print install instructions and exit.

---

## Step 7 — `src/dinary_analytics/ai_service.py`

Replaces `mcp_server.py` as the primary entry point. `mcp_server.py` kept for import compatibility (re-exports `mcp` object only if needed by tests).

```
startup:
  1. register_on_vm1()           # ai_replica.py — add self, remove stale, rewrite config if needed
  2. restore_local_replica()     # Step 6 — local LTX → SQLite
     on LitestreamRestoreError: _sync_replica() fallback
  3. start FastMCP on --port (default 8765)
  4. start background thread: every SYNC_INTERVAL min → restore_local_replica()

shutdown (SIGTERM):
  5. stop background thread, stop FastMCP
```

`DINARY_AI_SYNC_INTERVAL_MINUTES`: env var, default 30.

---

## Step 8 — `tasks/dinary_ai.py`

- `inv setup-dinary-ai` — idempotent: registers current machine on VM1, installs and starts `dinary-ai` OS service. Safe to call repeatedly.
- `inv install-dinary-ai` — writes launchd plist (macOS) or Task Scheduler XML (Windows), enables, starts.
- `inv uninstall-dinary-ai` — stops and removes service entry.
- `inv list-dinary-ai-replicas` — SSHs to VM1, lists `/var/lib/dinary-replicas/*.json` with hostname and `last_seen`.
- `inv remove-dinary-ai-replica --hostname NAME` — removes entry, rebuilds config, restarts if changed.

launchd plist: `~/Library/LaunchAgents/dev.dinary.ai.plist`, `KeepAlive: true`, `RunAtLoad: true`.

Windows: Task Scheduler, trigger `AtLogon`, restart on failure 3×.

---

## Step 9 — Healthcheck: Litestream config error check

**File**: `tasks/healthcheck.py`

Add alongside existing `_litestream_error_check_command()`:

```python
def _litestream_config_error_check_command() -> str:
    return (
        "journalctl -u litestream --since '10 minutes ago' --no-pager -q "
        "| grep -i 'cannot open config\\|failed to parse\\|error loading' || true"
    )

def _parse_litestream_config_errors(output: str) -> list[str]:
    return output.strip().splitlines() if output.strip() else []
```

Called in `healthcheck --remote` after LTX error check. Message: `"litestream config error: {last_line}"`.

Tests: `TestLitestreamConfigErrorCheckCommand` — same pattern as existing `TestLitestreamErrorCheckCommand`.

---

## Done gate

- `uv run inv pre` → 0 errors
- `uv run pytest` → 0 failures
- Manual: `inv analytics` on clean machine auto-runs `setup-ai-replica`, `dinary-ai` starts, MCP reachable, Marimo opens.
