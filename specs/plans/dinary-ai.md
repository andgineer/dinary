# dinary-ai — implementation plan

Background service: MCP server + SQLite replica pulled from VM2 by a background daemon via SSH.
`inv analytics` detects whether `dinary-ai` is running and auto-configures it via `setup-dinary-ai`.

---

## Architecture summary

- `dinary-ai` serves FastMCP on HTTP.
- Background daemon restores the ledger replica by running `litestream restore` **locally** over SFTP against VM2's archive, immediately on startup and then every 30 minutes — independently of MCP requests. The dashboard's "Refresh now" button (Step 5a) can wake it early, so the interval only has to bound staleness for users who never click it. (VM1 = dinary server, runs the app and litestream; VM2 = storage server where litestream writes replicas; `dinary-ai` restores from VM2.)
- Restoring locally rather than reusing the existing `_build_replica_restore_script` / `ssh_replica_capture_bytes` pattern (which runs `litestream restore` *on VM2* and streams the resulting bytes back over SSH) is a deliberate choice: that pattern would put restore CPU/disk/bandwidth load on the small storage VM on every poll, multiplied across every user's laptop and the polling interval. Running `litestream` locally keeps VM2 in a passive file-serving role and moves that cost to the machine that benefits from it. The trade-off — accepted here — is a new local dependency (`litestream` binary, validated at setup time, Step 3) and a dedicated SFTP key path (`DINARY_SSH_KEY_PATH`).
- MCP tool handlers query the local replica directly (zero added latency) by resolving the path via `get_db_path()`. If the daemon has not yet produced a local DB, they return a FastMCP error immediately. The dashboard surfaces replication status (last-sync time, manual refresh) on every load — see Step 5a.
- `inv analytics` checks if `dinary-ai` is reachable; if not, runs `setup-dinary-ai` idempotently before opening Marimo.
- Cross-platform: macOS (launchd) and Windows (Task Scheduler).
- Litestream on VM1 is the single replication path — enhanced diagnostics to maximise visibility of failures.

---

## Step 1 — Local SQLite restore from VM2

Create new file `src/dinary_analytics/exceptions.py` and define `RestoreError`.

Redefine `REPLICA_PATH` in `src/dinary_analytics/paths.py` to be platform-specific — it stops being server-relative (`_DATA_DIR / "ledger-replica.db"`), because `dinary-ai` runs on the user's machine, independent of the repo and of `settings.data_path`:
- macOS: `~/Library/Application Support/dinary/dinary-ai.db`
- Windows: `Path(os.environ["LOCALAPPDATA"]) / "dinary" / "dinary-ai.db"` — wrap missing key in `RuntimeError("LOCALAPPDATA not set; cannot determine DB path on Windows")`

This is the only path consumers need to know about: `open_ledger()`'s existing `replica_path or REPLICA_PATH` default now resolves to the daemon-restored file, so every existing call site — dashboard cells, `tags.py`, `events.py`, `views.py`, and the MCP `query` handler — keeps working unchanged (Step 2 covers the readiness gate that MCP handlers add on top). `sync_replica()` in `connection.py` and its only caller, `_sync_replica` in `tasks/analytics.py` (removed in Step 4), become dead code — delete `sync_replica()` and its tests in `tests/analytics/test_connection.py`.

`restore_replica() -> Path`:
- At function entry, checks `shutil.which("litestream")`; if missing, prints install instructions and raises `RestoreError` (not `SystemExit` — the caller decides whether to abort). `setup-dinary-ai` (Step 3) performs the same check *before* installing the service, so this branch only fires for a `litestream` binary removed after setup — the common "not installed yet" case is caught up front with actionable instructions instead of failing silently in the background.
- `db_path = REPLICA_PATH` (platform-specific path defined above). This is both the restore target and the return value.
- Resolves `.deploy/.env` relative to the repo root, not CWD: `_REPO_ROOT = Path(__file__).resolve().parents[2]`, `_ENV_PATH = _REPO_ROOT / ".deploy" / ".env"`. This mirrors the anchoring `dinary.config` already uses for the same reason — "so cron, systemd, and interactive `uv run` all agree regardless of CWD" — which matters here because launchd/Task Scheduler start the daemon with an unpredictable working directory. Reads `DINARY_REPLICA_HOST` (e.g. `ubuntu@hostname`) via `dotenv_values(_ENV_PATH)`. Splits the value on `"@"` via `split("@", 1)`; raises `RestoreError("DINARY_REPLICA_HOST must be in user@host format")` if the result does not yield exactly two non-empty parts. (`tasks.devtools.env.replica_host()` already reads and validates the same variable — the parsing is intentionally re-implemented here rather than imported, because `dinary_analytics` must not depend on `tasks`.) Reads optional `DINARY_SSH_KEY_PATH` from the same file. Writes a temporary litestream YAML config via `tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False)` (`delete=False` is required on Windows — a `NamedTemporaryFile` open for writing cannot be opened by a child process on Windows unless the handle is released first; the file is deleted in the `finally` block instead). Config contains `type: sftp`, `host`, `user`, `path: /var/lib/litestream/dinary` — a literal that mirrors `REPLICA_LITESTREAM_DIR` / `REPLICA_DB_NAME` in `tasks/devtools/constants.py`; add a comment naming those two constants so a future change to VM2's layout is easy to find from this side too. Includes `key-path` only if `DINARY_SSH_KEY_PATH` is set — otherwise litestream falls back to the SSH agent and default key discovery. Runs `litestream restore -config <tempfile> <db_path>` via `subprocess.run`. Deletes the temp file in a `finally` block. Add to top-level imports: `import os`, `import shutil`, `import subprocess`, `import tempfile`; `from dotenv import dotenv_values`.
- Returns `db_path`.
- Raises `RestoreError` on failure; caller logs warning and serves stale data.

All of the following lives in `src/dinary_analytics/restore.py` (same file as `restore_replica()`).

Imports at top level: `import datetime`, `import threading`, `import time`.

Constants:
```python
RESTORE_INTERVAL_SECONDS = 1800  # 30 minutes — normal poll interval after success; manual "Refresh now" (Step 2/5a) wakes the loop early, so this only needs to bound staleness for users who never click it
RESTORE_RETRY_BASE_SECONDS = 30  # first retry after error; doubles each failure, capped at RESTORE_INTERVAL_SECONDS
```

Module-level state (`_db_path`, `_last_restore`, `_last_restore_error` mutated only inside `_restore_loop`; `_daemon_thread` mutated only in `start_restore_daemon()`; `_wake_event` set from `trigger_restore_now()` and cleared inside `_restore_loop`; all read only via accessors):
```python
_lock: threading.Lock = threading.Lock()
_db_path: Path | None = None        # None = no successful restore yet
_last_restore: float | None = None  # time.time() timestamp
_last_restore_error: str | None = None
_daemon_thread: threading.Thread | None = None
_wake_event: threading.Event = threading.Event()  # set by trigger_restore_now() to cut the current wait short
```

`_restore_loop() -> None`:
- Declares `global _db_path, _last_restore, _last_restore_error` at function top.
- Tracks a local `retry_delay: int` initialised to `RESTORE_RETRY_BASE_SECONDS`.
- Runs forever: calls `restore_replica()`.
  - **Success**: acquires `_lock` to update `_db_path`, `_last_restore`, and set `_last_restore_error = None` atomically; resets `retry_delay = RESTORE_RETRY_BASE_SECONDS`; waits `_wake_event.wait(timeout=RESTORE_INTERVAL_SECONDS)` then `_wake_event.clear()`.
  - **`RestoreError`**: logs warning; acquires `_lock` to set `_last_restore_error` and leave `_db_path` unchanged (stale or `None`); waits `_wake_event.wait(timeout=retry_delay)` then `_wake_event.clear()`; then doubles `retry_delay` capped at `RESTORE_INTERVAL_SECONDS`.
- Both branches wait via `_wake_event` rather than `time.sleep` so `trigger_restore_now()` can cut either one short — the loop doesn't care whether `wait()` returned because of the timeout or because the event was set; either way it proceeds straight to the next `restore_replica()` call. Always `clear()` immediately after waking, so a stale "set" from a previous trigger can't cause a busy-loop.

`trigger_restore_now() -> None`:
- Sets `_wake_event`. Idempotent and safe to call while a restore is already running in `restore_replica()`: the event is only consulted (and cleared) inside the wait, so a `set()` that arrives mid-restore simply makes the *next* wait return immediately — i.e. "restore again right away once this one finishes," which is the desired behaviour for a user mashing the refresh button, not a bug to guard against.

`get_db_path() -> Path | None`:
- Acquires `_lock` and returns `_db_path`. Used by MCP tool handlers and `/health` — avoids capturing `None` at import time.

`get_last_restore() -> float | None`:
- Acquires `_lock` and returns `_last_restore`.

`get_last_restore_error() -> str | None`:
- Acquires `_lock` and returns `_last_restore_error`.

`start_restore_daemon() -> None`:
- Declares `global _daemon_thread` at function top.
- Guard: if `_daemon_thread is not None and _daemon_thread.is_alive()`, returns immediately — prevents two concurrent restore loops if called twice.
- Spawns `threading.Thread(target=_restore_loop, daemon=True)`, assigns to `_daemon_thread`, and starts it. The first restore runs immediately (before the first sleep).

MCP tool handlers call `get_db_path()`; no restore is triggered inside a handler.

Tests in `tests/analytics/test_restore.py`:
- `test_restore_replica_returns_path` — returns `Path` on successful restore
- `test_restore_replica_raises_on_failure` — raises `RestoreError` when litestream fails
- `test_restore_loop_sets_db_path` — after one iteration `_db_path` is set to the returned path
- `test_restore_loop_logs_on_error` — `RestoreError` on first iteration logged; `_db_path` stays `None`; loop continues
- `test_restore_loop_keeps_stale_path_on_error` — after a successful restore followed by `RestoreError`, `get_db_path()` returns the previous (stale) path, not `None`
- `test_restore_loop_retries_after_failure` — loop calls `restore_replica()` again after a failed attempt
- `test_restore_loop_backoff_doubles_on_repeated_errors` — sleep intervals double on consecutive `RestoreError`s (30 → 60 → 120 …), capped at `RESTORE_INTERVAL_SECONDS`
- `test_restore_loop_resets_delay_after_success` — after a sequence of errors followed by a success, the next sleep is `RESTORE_INTERVAL_SECONDS`, not the accumulated backoff value
- `test_trigger_restore_now_wakes_loop_immediately` — calling `trigger_restore_now()` while `_restore_loop` is waiting (either the normal interval or a retry backoff) causes the next `restore_replica()` call without waiting out the full timeout
- `test_start_restore_daemon_spawns_daemon_thread` — `start_restore_daemon()` creates a thread with `daemon=True` and starts it
- `test_get_last_restore_returns_timestamp` — after a successful restore, `get_last_restore()` returns the `time.time()` value set during that restore
- `test_get_last_restore_error_returns_message` — after a `RestoreError`, `get_last_restore_error()` returns the error string; after a subsequent success it returns `None`

---

## Step 2 — `src/dinary_analytics/ai_service.py`

`MCP_PORT: int = 8765` lives in `src/dinary_analytics/paths.py` — the single source of truth imported by `ai_service.py`, `tasks/analytics.py`, and `notebooks/dashboard.py`. It belongs in `paths.py` rather than in `ai_service.py` itself: `tasks/analytics.py` is imported by `tasks/__init__.py` on *every* `inv` invocation, and importing `ai_service` to read a port number would execute its full module body — constructing `FastMCP`, registering every `@mcp.tool()`, and pulling in `duckdb` / `mcp` / `starlette` / `uvicorn` through `connection.py` — turning `inv pre`, `inv dev`, and every other task into one that loads the entire MCP+analytics stack. `paths.py` is already documented as having no heavy deps and being safe to import anywhere.

Replaces `mcp_server.py` as the primary entry point. Delete `mcp_server.py`; rename `tests/analytics/test_mcp_server.py` to `tests/analytics/test_ai_service.py` (if it exists) and update imports to `dinary_analytics.ai_service`. Retain all MCP tools from `mcp_server.py` — only the module structure, replica-path resolution, and startup sequence change.

```
startup:
  1. start_restore_daemon()   # spawns background thread; first restore runs immediately
  2. start FastMCP on --port (default 8765)

shutdown (SIGTERM):
  3. stop FastMCP
```

MCP tool handlers do not trigger any restore. `_run_query` (and any future ledger-reading tool) calls `get_db_path()` from `dinary_analytics.restore` first; if `None`, returns a FastMCP error immediately (replica not yet available) — *before* touching `open_ledger`. When `get_db_path()` returns a `Path`, the handler passes it explicitly to `open_ledger(path)`. (The zero-arg `open_ledger()` still works for notebooks and other non-MCP callers — its `REPLICA_PATH` default now resolves to the same daemon-restored file, see Step 1 — but MCP handlers go through `get_db_path()` because only it carries the "has a restore ever succeeded" signal they need to gate on.) Do not let `sqlite3` raise an unhandled exception to the client.

`GET /health` is registered via `@mcp.custom_route("/health", methods=["GET"])`. The handler is `async`, accepts a `starlette.requests.Request`, and returns a `starlette.responses.JSONResponse`. Add `import argparse`, `import datetime`, `from starlette.requests import Request`, and `from starlette.responses import JSONResponse` to module-level imports. The handler calls `get_db_path()`, `get_last_restore()`, and `get_last_restore_error()` from `dinary_analytics.restore`. Returns `{"ok": bool, "last_restore": "ISO8601" | null, "error": "..." | null}`. `ok` is `true` when `get_db_path() is not None` (replica available and queries can be served, even if data is stale); `false` when no restore has ever succeeded and the service cannot serve any data. Clients check `ok` for query-ability; `error` is diagnostic only and does not flip `ok` to `false`. `last_restore` is `null` when no restore has completed; otherwise `datetime.datetime.fromtimestamp(get_last_restore(), tz=datetime.timezone.utc).isoformat()`.

`POST /restore/now` is registered via `@mcp.custom_route("/restore/now", methods=["POST"])` — the "Refresh now" button in the dashboard (Step 5a) calls it to force an immediate restore instead of waiting for the next scheduled poll (now `RESTORE_INTERVAL_SECONDS = 1800`, Step 1). The handler is `async`, calls `trigger_restore_now()` from `dinary_analytics.restore`, and returns `JSONResponse({"triggered": True})` immediately — it does not wait for the restore to finish. The caller polls `GET /health` for the updated `last_restore`.

`main()` is defined in `ai_service.py`:
```python
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=MCP_PORT)
    args = parser.parse_args()
    start_restore_daemon()
    mcp.run(transport="streamable-http", host="127.0.0.1", port=args.port)
```

`ai_service.py` must end with:
```python
if __name__ == "__main__":
    main()
```
so that `python -m dinary_analytics.ai_service` works (required by the launchd plist and Task Scheduler XML in Step 3).

`_ensure_dinary_ai` (Step 4) waits only for HTTP reachability, not replica readiness — the service responds to `/health` before the first restore completes. If `get_db_path()` is still `None` when Marimo opens, the notebook's top-cell health check (Step 5a) shows the "replica not ready" error and halts rendering. No special handling is needed in `_ensure_dinary_ai`.

Tests in `tests/analytics/test_ai_service.py`:
- `test_startup_restore_failure_still_starts` — `RestoreError` on startup is logged; service starts with `get_db_path() == None`
- `test_tool_handler_returns_error_when_no_db` — handler returns FastMCP error when `get_db_path()` returns `None`
- `test_health_ok` — returns `{"ok": true}` after successful restore
- `test_health_degraded_no_db` — returns `{"ok": false}` when no restore has ever succeeded
- `test_health_ok_with_stale_data` — returns `{"ok": true, "error": "..."}` when `_db_path` is set but last sync failed
- `test_restore_now_triggers_loop` — `POST /restore/now` calls `trigger_restore_now()` and returns `{"triggered": true}` without blocking for the restore to finish

---

## Step 3 — `tasks/dinary_ai.py`

- `inv setup-dinary-ai` — idempotent: first checks `shutil.which("litestream")` and aborts with install instructions if it's missing (without this, the daemon would install and start successfully, then fail silently in its background thread — see Step 1). Then calls `install-dinary-ai` if the plist/task does not already exist (macOS: check plist file presence; Windows: `schtasks /query /tn dinary-ai` exit code 0 = exists). Finally ensures the service is actually running:
  - macOS: `launchctl kickstart -k gui/$(id -u)/dev.dinary.ai` — works whether the agent is freshly loaded, loaded-but-stopped, or already running (`-k` restarts a running instance, so a freshly-written plist's changes take effect too).
  - Windows: `schtasks /query /tn dinary-ai /fo csv` to read the `Status` column; runs `schtasks /run /tn dinary-ai` only when it reads anything other than `Running`.

  Safe to call repeatedly.
- `inv install-dinary-ai` — writes launchd plist (macOS) or Task Scheduler XML (Windows), then activates: macOS calls `launchctl load ~/Library/LaunchAgents/dev.dinary.ai.plist`; Windows calls `schtasks /run /tn dinary-ai`.
- `inv uninstall-dinary-ai` — macOS: `launchctl unload <plist>` then deletes the plist file. Windows: `schtasks /delete /tn dinary-ai /f`.

launchd plist: `~/Library/LaunchAgents/dev.dinary.ai.plist`, `KeepAlive: true`, `RunAtLoad: true`, `WorkingDirectory: <repo_root>`. The working directory is required, not optional: launchd starts agents with `cwd=/`, and `uv run` resolves `pyproject.toml` and the project's venv relative to cwd — without `WorkingDirectory` the daemon fails to start. `<repo_root>` is computed the same way as elsewhere in `tasks/`: `Path(__file__).resolve().parents[1]` from `tasks/dinary_ai.py`.
`ProgramArguments`: `[<uv_path>, "run", "python", "-m", "dinary_analytics.ai_service", "--port", str(MCP_PORT)]` where `<uv_path>` is the absolute path resolved at install time via `shutil.which("uv")` (raises `RuntimeError` if not found). Using the absolute path is required because launchd runs agents with a restricted `PATH` that typically does not include `~/.local/bin` or Homebrew prefixes where `uv` lives. `MCP_PORT` is imported from `dinary_analytics.paths` at install time — do not hardcode `8765`.

Windows: Task Scheduler, trigger `AtLogon`, restart on failure 3×, `<WorkingDirectory>` set to `<repo_root>` (same value, same reasoning as the plist's `WorkingDirectory` — `uv run` needs cwd to resolve the project). The XML config is written to a `tempfile.NamedTemporaryFile(suffix=".xml", delete=False)`, passed to `schtasks /create /xml <tempfile>`, then deleted. The task definition is stored in the Windows Task Scheduler database — the XML file itself is not kept. Permanent task name: `dinary-ai`.

Tests in `tests/tasks/test_dinary_ai.py`:
- `test_setup_dinary_ai_aborts_without_litestream` — `setup-dinary-ai` aborts with install instructions when `shutil.which("litestream")` is `None`, and writes no plist / starts nothing
- `test_setup_dinary_ai_idempotent` — calling twice does not raise and plist still exists with correct content on macOS
- `test_install_writes_plist` — plist file written to correct path with expected keys, including `WorkingDirectory`, on macOS
- `test_uninstall_removes_plist` — plist file removed and service stopped on macOS
- `test_install_writes_xml_windows` — `schtasks /create` is called with an XML temp file whose content includes `AtLogon` trigger, `WorkingDirectory`, and 3× restart-on-failure policy; the temp file is deleted afterwards
- `test_uninstall_removes_task_windows` — scheduled task removed and service stopped on Windows

---

## Step 4 — Remove MCP server from `inv analytics`

**File**: `tasks/analytics.py`

Remove:
- `_DEFAULT_MCP_PORT` constant, `mcp_port` task parameter and its `@task(help=...)` entry, `_dinary_ai_running` function
- `_sync_replica` function
- Imports that become unused: `REPLICA_PATH`, `_build_replica_restore_script`, `ssh_replica_capture_bytes`

Add at module top level (new imports):
```python
import time
import urllib.error
import urllib.request
from dinary_analytics.paths import MCP_PORT
```

Add function (port is always `MCP_PORT` — no parameter):
```python
def _ensure_dinary_ai(c) -> None:
    try:
        urllib.request.urlopen(f"http://localhost:{MCP_PORT}/health", timeout=2)
        print(f"OK: dinary-ai reachable on port {MCP_PORT}")
        return
    except (urllib.error.URLError, OSError):
        pass
    print(f"dinary-ai not running on port {MCP_PORT} — running setup-dinary-ai")
    c.run("uv run inv setup-dinary-ai")
    for _ in range(30):
        time.sleep(1)
        try:
            urllib.request.urlopen(f"http://localhost:{MCP_PORT}/health", timeout=2)
            print(f"OK: dinary-ai reachable on port {MCP_PORT}")
            return
        except (urllib.error.URLError, OSError):
            pass
    raise SystemExit(f"dinary-ai did not start on port {MCP_PORT} after setup")
```

`analytics` calls `_ensure_dinary_ai(c)` before opening Marimo. `pty=False` (default) — `setup-dinary-ai` is non-interactive. Replica-readiness is not checked here — if the replica is not yet ready when Marimo opens, the notebook's top-cell health check (Step 5a) surfaces the error.

`analytics` task signature loses `--mcp-port`; keeps `--port` for Marimo only.

---

## Step 5 — Litestream diagnostics

Litestream on VM1 is the single replication path for `dinary-ai`. Failures must be maximally visible.

### 5a — Marimo notebook shows replication status and blocks on replica error

Top cell of `src/dinary_analytics/notebooks/dashboard.py` becomes a visible **replication-status banner** that stays at the top of the dashboard, not just a silent gate: it still halts everything below it on error, but in the healthy case it renders something the user sees on every load — last-sync time plus a manual refresh control — and lets the rest of the notebook render beneath it.

State (declared alongside the notebook's other `mo.state` calls, e.g. near `draft_view`/`view_list_ver`): `refresh_tick, bump_refresh = mo.state(0)`.

The cell, on every run:
1. Imports `MCP_PORT` from `dinary_analytics.paths`; reads `refresh_tick()`.
2. If `refresh_tick() > 0` — i.e. this run was triggered by the "Refresh now" button in step 5 below, not the initial load — first calls `urllib.request.urlopen(f"http://localhost:{MCP_PORT}/health")` to capture the *current* `last_restore` value, then `POST`s to `http://localhost:{MCP_PORT}/restore/now` (fire-and-forget — `trigger_restore_now()` wakes the daemon and the route returns immediately, Step 2), then polls `GET /health` once a second up to 30 times — the same bound and `urllib.request.urlopen` client as `_ensure_dinary_ai` — until the freshly-fetched `last_restore` differs from the captured value, or the loop is exhausted. Whichever `/health` payload was fetched last (the polled one on a refresh run, a single fresh `GET` otherwise) feeds steps 3–5; `json.loads` parses it (already imported in the notebook's top cell).
3. `urllib.error.URLError` (service not running) halts everything below: `mo.stop(True, mo.callout(mo.md("**dinary-ai not running** — run `inv analytics`"), kind="danger"))`.
4. `ok: false` in the parsed response (no restore ever succeeded) also halts everything below: reads `error` from the same payload and calls `mo.stop(True, mo.callout(mo.md(f"**Replica not ready:** {error}"), kind="danger"))`.
5. Otherwise renders the banner and lets the notebook continue — e.g. `mo.hstack([mo.md(f"🔄 Replica synced **{_format_ago(last_restore)}**"), mo.ui.button(label="Refresh now", on_click=lambda _: bump_refresh(refresh_tick() + 1))])`. `_format_ago` is a small helper near the notebook's other formatting helpers that turns the ISO 8601 `last_restore` into "just now" / "5 minutes ago" / "2 hours ago" relative to `datetime.datetime.now(tz=datetime.timezone.utc)`.

Clicking "Refresh now" bumps `refresh_tick`, which re-runs this cell from step 1 with the new value — triggering an immediate restore and updating the banner once the poll in step 2 sees a fresh `last_restore` (or once its 30-second bound is hit, whichever comes first; on a timeout the banner simply keeps showing the latest known value, which by then may already have moved).

Once this gate passes for the first time, `get_db_path()` is guaranteed non-`None` for the rest of the notebook's lifetime — `_db_path` is only ever replaced with a fresher path or left stale, never reset to `None` (Step 1). Cell bodies elsewhere in the dashboard need no changes for this step: their existing zero-arg `open_ledger()` / `load_view_frame(...)` calls resolve `REPLICA_PATH` to the daemon-restored file (Step 1).

In the normal flow `_ensure_dinary_ai` (Step 4) guarantees the service is running before Marimo opens; the `URLError` branch is a safety net for notebooks opened manually.

Because the user can now force a sync on demand, the background poll only has to bound staleness for people who never click the button — hence `RESTORE_INTERVAL_SECONDS` moves from 10 minutes to 30 (`1800`, Step 1).

### 5b — Healthcheck: Litestream config errors

**File**: `tasks/healthcheck.py`

Add alongside existing `_litestream_error_check_command()`, reusing its `'24 hours ago'` window rather than a shorter one: `healthcheck --remote` is run on demand (see the existing check immediately above and the "catches a missed run within hours" reasoning behind `inv backup-cloud-status`'s cadence in the operations docs), so a 10-minute window would risk missing a config error that fired between two manual runs.

```python
def _litestream_config_error_check_command() -> str:
    return (
        "journalctl -u litestream --since '24 hours ago' --no-pager -q "
        "| grep -i 'cannot open config\\|failed to parse\\|error loading' || true"
    )

def _parse_litestream_config_errors(output: str) -> list[str]:
    return output.strip().splitlines() if output.strip() else []
```

Called in `healthcheck --remote` after existing LTX error check. Follows the same surfacing pattern as the existing LTX check: if `_parse_litestream_config_errors(output)` returns a non-empty list, surface `"litestream config error: {first_line}"` via the same error-reporting path used by the existing check. First line is the root cause; subsequent lines are stack context.

Tests: `TestLitestreamConfigErrorCheckCommand` — same pattern as existing `TestLitestreamErrorCheckCommand`.

---

## Step 6 — Update `specs/reference/analytics-ai.md`

The spec currently documents the architecture this plan replaces: `mcp_server.py` as the entry point, `ledger-replica.db` synced into `.analytics/` "on every `inv analytics` run", and the flow "1. Sync replica → 2. Start MCP server → 3. Open dashboard". Update it to describe the new architecture as current state only (no before/after — see spec conventions):
- Package structure: `mcp_server.py` → `ai_service.py`
- Storage: the replica lives at the platform-specific path the background daemon restores into (Step 1), refreshed independently of `inv analytics` on its own schedule (and on demand via the dashboard's refresh control, Step 5a) — not synced once per run into `.analytics/`
- `## inv analytics`: ensures `dinary-ai` is reachable (auto-installing it via `setup-dinary-ai` when it isn't) and opens the dashboard; replication is the daemon's responsibility, not a step of this flow
- `## MCP server`: tool list is unchanged — just confirm the section no longer names `mcp_server.py`

---

## Done gate

- `uv run inv pre` → 0 errors
- `uv run pytest` → 0 failures
- Manual: `inv analytics` on clean machine auto-runs `setup-dinary-ai`, `dinary-ai` starts, MCP reachable, Marimo opens.
