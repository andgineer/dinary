# dinary-ai — implementation plan

Background service: MCP server + SQLite replica pulled from VM2 on demand via SSH.
`inv analytics` detects whether `dinary-ai` is running and auto-configures it via `setup-dinary-ai`.

---

## Architecture summary

- `dinary-ai` serves FastMCP on HTTP.
- On startup and before each MCP query (with 5 s cooldown): pulls snapshot + WAL segments from VM2 via SSH (`litestream restore`), same infrastructure as `_sync_replica()`.
- `inv analytics` checks if `dinary-ai` is reachable; if not, runs `setup-dinary-ai` idempotently before opening Marimo.
- Cross-platform: macOS (launchd) and Windows (Task Scheduler).
- Litestream on VM1 is the single replication path — enhanced diagnostics to maximise visibility of failures.

---

## Step 1 — Remove MCP server from `inv analytics`

**File**: `tasks/analytics.py`

Remove: `subprocess`, `mcp_proc`, `_DEFAULT_MCP_PORT`, try/finally MCP start/stop block.

Add before opening Marimo (imports at module top level):
```python
import urllib.error
import urllib.request

_DEFAULT_AI_PORT = 8765

def _ensure_dinary_ai(c, mcp_port: int) -> None:
    try:
        urllib.request.urlopen(f"http://localhost:{mcp_port}/health", timeout=2)
        print(f"OK: dinary-ai reachable on port {mcp_port}")
    except (urllib.error.URLError, OSError):
        print(f"dinary-ai not running on port {mcp_port} — running setup-dinary-ai")
        c.run("uv run inv setup-dinary-ai", pty=True)
```

`analytics` calls `_ensure_dinary_ai(c, _DEFAULT_AI_PORT)` before opening Marimo.

`analytics` task signature loses `--mcp-port`; keeps `--port` for Marimo only.

---

## Step 2 — Local SQLite restore from VM2

Define `RestoreError` in `src/dinary_analytics/exceptions.py`.

Platform DB paths (in `src/dinary_analytics/paths.py`):
- macOS: `~/.local/share/dinary/dinary-ai.db`
- Windows: `%LOCALAPPDATA%/dinary/dinary-ai.db`

`restore_replica() -> Path`:
- Runs `litestream restore` with VM2 as SFTP source (snapshot + WAL segments), reusing connection params from `_sync_replica()` infrastructure.
- Returns path to local SQLite.
- Raises `RestoreError` on failure; caller logs warning and serves stale data.

Litestream binary check: `shutil.which("litestream")` at startup; if missing, print install instructions and exit.

Module-level state (imports at top level: `import threading`, `import time`):
```python
_restore_lock = threading.Lock()
_last_restore: float = 0.0
RESTORE_COOLDOWN_SECONDS = 5
```

`maybe_restore() -> None`:
- Acquires `_restore_lock` before checking and updating `_last_restore` to avoid concurrent restores under parallel MCP requests.
- Runs `restore_replica()` only if `time.monotonic() - _last_restore > RESTORE_COOLDOWN_SECONDS`.
- On `RestoreError`: logs warning, does not raise (service continues with stale data).

Tests in `tests/analytics/test_restore.py`:
- `test_restore_replica_returns_path` — returns `Path` on successful restore
- `test_restore_replica_raises_on_failure` — raises `RestoreError` when litestream fails
- `test_maybe_restore_cooldown` — second call within 5 s skips restore; call after 5 s triggers it
- `test_maybe_restore_logs_on_error` — `RestoreError` logged, not re-raised
- `test_maybe_restore_no_concurrent_restores` — concurrent calls trigger only one restore

---

## Step 3 — `src/dinary_analytics/ai_service.py`

Replaces `mcp_server.py` as the primary entry point. Delete `mcp_server.py`; update any existing tests to import directly from `dinary_analytics.ai_service`.

```
startup:
  1. maybe_restore()          # initial pull from VM2; RestoreError logged, not re-raised
  2. start FastMCP on --port (default 8765)

shutdown (SIGTERM):
  3. stop FastMCP
```

Each MCP tool handler calls `maybe_restore()` before querying the DB.

`ai_service.py` tracks last restore result: timestamp + error message (or `None` on success).
Exposed as `GET /health` returning JSON `{"ok": bool, "last_restore": "ISO8601", "error": "..." | null}`.

Tests in `tests/analytics/test_ai_service.py`:
- `test_startup_restore_failure_still_starts` — `RestoreError` on startup is logged; service starts with no local DB
- `test_tool_handler_calls_maybe_restore` — each MCP tool calls `maybe_restore()` before querying
- `test_health_ok` — returns `{"ok": true}` after successful restore
- `test_health_degraded` — returns `{"ok": false, "error": "..."}` after `RestoreError`

---

## Step 4 — `tasks/dinary_ai.py`

- `inv setup-dinary-ai` — idempotent: installs and starts `dinary-ai` OS service. Safe to call repeatedly.
- `inv install-dinary-ai` — writes launchd plist (macOS) or Task Scheduler XML (Windows), enables, starts.
- `inv uninstall-dinary-ai` — stops and removes service entry.

launchd plist: `~/Library/LaunchAgents/dev.dinary.ai.plist`, `KeepAlive: true`, `RunAtLoad: true`.
`ProgramArguments`: `["uv", "run", "python", "-m", "dinary_analytics.ai_service", "--port", "8765"]` with the absolute path to `uv` resolved at install time via `shutil.which("uv")`.

Windows: Task Scheduler, trigger `AtLogon`, restart on failure 3×.

Tests in `tests/tasks/test_dinary_ai.py`:
- `test_setup_dinary_ai_idempotent` — calling twice does not raise
- `test_install_writes_plist` — plist file written to correct path with expected keys on macOS
- `test_uninstall_removes_plist` — plist file removed and service stopped on macOS

---

## Step 5 — Litestream diagnostics

Litestream on VM1 is the single replication path for `dinary-ai`. Failures must be maximally visible.

### 5a — Marimo notebook blocks on replica error

Top cell of `src/dinary_analytics/notebooks/dashboard.py` calls `GET http://localhost:8765/health`. On `ok: false` — calls `mo.stop(True, mo.callout(mo.md(f"**Replica error:** {error}"), kind="danger"))`, halting all subsequent cells. The notebook shows only the error; nothing else renders until the replica is fixed.

### 5b — Healthcheck: Litestream config errors

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

Called in `healthcheck --remote` after existing LTX error check. Message: `"litestream config error: {last_line}"`.

Tests: `TestLitestreamConfigErrorCheckCommand` — same pattern as existing `TestLitestreamErrorCheckCommand`.

---

## Done gate

- `uv run inv pre` → 0 errors
- `uv run pytest` → 0 failures
- Manual: `inv analytics` on clean machine auto-runs `setup-dinary-ai`, `dinary-ai` starts, MCP reachable, Marimo opens.
