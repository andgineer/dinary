# dinary-ai — implementation plan

Background service: MCP server + SQLite replica pulled from VM2 by a background daemon via SSH.
`inv analytics` detects whether `dinary-ai` is running and auto-configures it via `setup-dinary-ai`.

---

## Architecture summary

- `dinary-ai` serves FastMCP on HTTP.
- Background daemon pulls snapshot + WAL segments from VM2 via SSH (`litestream restore`) immediately on startup, then every 10 minutes — independently of MCP requests.
- MCP tool handlers query the local replica directly (zero added latency). If the daemon has not yet produced a local DB, they return a FastMCP error immediately.
- `inv analytics` checks if `dinary-ai` is reachable; if not, runs `setup-dinary-ai` idempotently before opening Marimo.
- Cross-platform: macOS (launchd) and Windows (Task Scheduler).
- Litestream on VM1 is the single replication path — enhanced diagnostics to maximise visibility of failures.

---

## Step 1 — Remove MCP server from `inv analytics`

**File**: `tasks/analytics.py`

Remove: `subprocess`, `mcp_proc`, `_DEFAULT_MCP_PORT`, try/finally MCP start/stop block.

Add before opening Marimo (imports at module top level):
```python
import json
import time
import urllib.error
import urllib.request
from dinary_analytics.ai_service import MCP_PORT

def _wait_replica_ready(mcp_port: int, timeout: int = 30) -> None:
    for i in range(timeout):
        try:
            with urllib.request.urlopen(f"http://localhost:{mcp_port}/health", timeout=2) as resp:
                data = json.loads(resp.read())
            if data.get("ok"):
                return
            if i == 0:
                print("dinary-ai is up but replica not yet ready — waiting …")
        except (urllib.error.URLError, OSError, ValueError):
            pass
        time.sleep(1)
    raise SystemExit(
        f"dinary-ai replica not ready after {timeout}s — check `inv healthcheck` for errors"
    )

def _ensure_dinary_ai(c, mcp_port: int) -> None:
    try:
        urllib.request.urlopen(f"http://localhost:{mcp_port}/health", timeout=2)
        print(f"OK: dinary-ai reachable on port {mcp_port}")
        _wait_replica_ready(mcp_port)
        return
    except (urllib.error.URLError, OSError):
        pass
    print(f"dinary-ai not running on port {mcp_port} — running setup-dinary-ai")
    c.run("uv run inv setup-dinary-ai", pty=True)
    for _ in range(10):
        time.sleep(1)
        try:
            urllib.request.urlopen(f"http://localhost:{mcp_port}/health", timeout=2)
            print(f"OK: dinary-ai reachable on port {mcp_port}")
            _wait_replica_ready(mcp_port)
            return
        except (urllib.error.URLError, OSError):
            pass
    raise SystemExit(f"dinary-ai did not start on port {mcp_port} after setup")
```

`analytics` calls `_ensure_dinary_ai(c, MCP_PORT)` before opening Marimo.

`analytics` task signature loses `--mcp-port`; keeps `--port` for Marimo only.

---

## Step 2 — Local SQLite restore from VM2

Define `RestoreError` in `src/dinary_analytics/exceptions.py`.

Platform DB paths (in `src/dinary_analytics/paths.py`):
- macOS: `~/.local/share/dinary/dinary-ai.db`
- Windows: `Path(os.environ["LOCALAPPDATA"]) / "dinary" / "dinary-ai.db"` — wrap missing key in `RuntimeError("LOCALAPPDATA not set; cannot determine DB path on Windows")`

`restore_replica() -> Path`:
- Runs `litestream restore` with VM2 as SFTP source (snapshot + WAL segments). Reads `DINARY_REPLICA_HOST` (e.g. `ubuntu@hostname`) from `.deploy/.env` via `dotenv_values`. Splits the value into `user` and `host` fields. Writes a temporary litestream YAML config (via `tempfile.NamedTemporaryFile`) with `type: sftp`, `host`, `user`, `key-path: ~/.ssh/id_ed25519`, `path: /var/lib/litestream/dinary`. Runs `litestream restore -config <tempfile> <db_path>` via `subprocess.run`. Deletes the temp file in a `finally` block. Add to top-level imports: `import subprocess`, `import tempfile`; `from dotenv import dotenv_values`.
- Returns path to local SQLite.
- Raises `RestoreError` on failure; caller logs warning and serves stale data.

Litestream binary check: `shutil.which("litestream")` at startup; if missing, print install instructions and exit.

All of the following lives in `src/dinary_analytics/restore.py` (same file as `restore_replica()`).

Imports at top level: `import datetime`, `import threading`, `import time`.

Constant:
```python
RESTORE_INTERVAL_SECONDS = 600  # 10 minutes
```

Module-level state (mutated only inside `_restore_loop`, read only via accessors):
```python
_lock: threading.Lock = threading.Lock()
_db_path: Path | None = None        # None = no successful restore yet
_last_restore: float | None = None  # time.time() timestamp
_last_restore_error: str | None = None
```

`_restore_loop() -> None`:
- Declares `global _db_path, _last_restore, _last_restore_error` at function top.
- Runs forever: calls `restore_replica()`, acquires `_lock` to update the three globals atomically, then `time.sleep(RESTORE_INTERVAL_SECONDS)`.
- On `RestoreError`: logs warning, acquires `_lock` to set `_last_restore_error` and leave `_db_path` unchanged (stale or `None`), then sleeps and retries.

`get_db_path() -> Path | None`:
- Acquires `_lock` and returns `_db_path`. Used by MCP tool handlers and `/health` — avoids capturing `None` at import time.

`get_last_restore() -> float | None`:
- Acquires `_lock` and returns `_last_restore`.

`get_last_restore_error() -> str | None`:
- Acquires `_lock` and returns `_last_restore_error`.

`start_restore_daemon() -> None`:
- Spawns `threading.Thread(target=_restore_loop, daemon=True)` and starts it. The first restore runs immediately (before the first sleep).

MCP tool handlers call `get_db_path()`; no restore is triggered inside a handler.

Tests in `tests/analytics/test_restore.py`:
- `test_restore_replica_returns_path` — returns `Path` on successful restore
- `test_restore_replica_raises_on_failure` — raises `RestoreError` when litestream fails
- `test_restore_loop_sets_db_path` — after one iteration `_db_path` is set to the returned path
- `test_restore_loop_logs_on_error` — `RestoreError` on first iteration logged; `_db_path` stays `None`; loop continues
- `test_restore_loop_keeps_stale_path_on_error` — after a successful restore followed by `RestoreError`, `get_db_path()` returns the previous (stale) path, not `None`
- `test_restore_loop_retries_after_failure` — loop calls `restore_replica()` again after a failed attempt
- `test_start_restore_daemon_spawns_daemon_thread` — `start_restore_daemon()` creates a thread with `daemon=True` and starts it

---

## Step 3 — `src/dinary_analytics/ai_service.py`

Define `MCP_PORT: int = 8765` at module top level — this is the single source of truth imported by `tasks/analytics.py` and `notebooks/dashboard.py`.

Replaces `mcp_server.py` as the primary entry point. Delete `mcp_server.py`; update `tests/analytics/test_mcp_server.py` (if it exists) to import from `dinary_analytics.ai_service`. Retain all MCP tools from `mcp_server.py` — only the module structure and startup sequence change.

```
startup:
  1. start_restore_daemon()   # spawns background thread; first restore runs immediately
  2. start FastMCP on --port (default 8765)

shutdown (SIGTERM):
  3. stop FastMCP
```

MCP tool handlers do not trigger any restore. Each handler calls `get_db_path()` from `dinary_analytics.restore`; if `None`, returns a FastMCP error immediately (replica not yet available). Do not let `sqlite3` raise an unhandled exception to the client.

`GET /health` calls `get_db_path()`, `get_last_restore()`, and `get_last_restore_error()` from `dinary_analytics.restore`. Returns `{"ok": bool, "last_restore": "ISO8601" | null, "error": "..." | null}`. `ok` is `true` when `get_db_path() is not None` (replica available, even if the most recent sync failed and stale data is being served); `false` when no restore has ever succeeded. `last_restore` is `null` when no restore has completed; otherwise `datetime.datetime.fromtimestamp(get_last_restore(), tz=datetime.timezone.utc).isoformat()`.

`_ensure_dinary_ai` (Step 1) waits only for HTTP reachability, not replica readiness — the service responds to `/health` before the first restore completes. If `get_db_path()` is still `None` when Marimo opens, the notebook's top-cell health check shows the "replica not ready" error (Step 5a) and halts rendering. No special handling is needed in `_ensure_dinary_ai`.

Tests in `tests/analytics/test_ai_service.py`:
- `test_startup_restore_failure_still_starts` — `RestoreError` on startup is logged; service starts with `get_db_path() == None`
- `test_tool_handler_returns_error_when_no_db` — handler returns FastMCP error when `get_db_path()` returns `None`
- `test_health_ok` — returns `{"ok": true}` after successful restore
- `test_health_degraded_no_db` — returns `{"ok": false}` when no restore has ever succeeded
- `test_health_ok_with_stale_data` — returns `{"ok": true, "error": "..."}` when `_db_path` is set but last sync failed

---

## Step 4 — `tasks/dinary_ai.py`

- `inv setup-dinary-ai` — idempotent: calls `install-dinary-ai` if the plist/task does not already exist, then starts the service if it is not already running. Safe to call repeatedly.
- `inv install-dinary-ai` — writes launchd plist (macOS) or Task Scheduler XML (Windows), enables, starts.
- `inv uninstall-dinary-ai` — stops and removes service entry.

launchd plist: `~/Library/LaunchAgents/dev.dinary.ai.plist`, `KeepAlive: true`, `RunAtLoad: true`.
`ProgramArguments`: `[<uv_path>, "run", "python", "-m", "dinary_analytics.ai_service", "--port", str(MCP_PORT)]` where `<uv_path>` is the absolute path resolved at install time via `shutil.which("uv")` (raises `RuntimeError` if not found). `MCP_PORT` is imported from `dinary_analytics.ai_service` at install time — do not hardcode `8765`.

Windows: Task Scheduler, trigger `AtLogon`, restart on failure 3×.

Tests in `tests/tasks/test_dinary_ai.py`:
- `test_setup_dinary_ai_idempotent` — calling twice does not raise and plist still exists with correct content on macOS
- `test_install_writes_plist` — plist file written to correct path with expected keys on macOS
- `test_uninstall_removes_plist` — plist file removed and service stopped on macOS
- `test_install_writes_xml_windows` — Task Scheduler XML written to correct path with `AtLogon` trigger and 3× restart-on-failure policy on Windows
- `test_uninstall_removes_task_windows` — scheduled task removed and service stopped on Windows

---

## Step 5 — Litestream diagnostics

Litestream on VM1 is the single replication path for `dinary-ai`. Failures must be maximally visible.

### 5a — Marimo notebook blocks on replica error

Top cell of `src/dinary_analytics/notebooks/dashboard.py` imports `MCP_PORT` from `dinary_analytics.ai_service` and calls `GET http://localhost:{MCP_PORT}/health`. On `ok: false` — calls `mo.stop(True, mo.callout(mo.md(f"**Replica error:** {error}"), kind="danger"))`, halting all subsequent cells. The notebook shows only the error; nothing else renders until the replica is fixed.

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
