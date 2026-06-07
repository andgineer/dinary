# dinary-ai — implementation plan

Background service: MCP server + SQLite replica refreshed by a background daemon via periodic
HTTP snapshot downloads from the dinary server.
`inv analytics` detects whether `dinary-ai` is running and auto-configures it via `setup-dinary-ai`.

---

## Architecture summary

- `dinary-ai` serves FastMCP on HTTP.
- Background daemon refreshes the local ledger replica by downloading a consistent snapshot over
  HTTP from the dinary server's `GET /api/analytics/db-snapshot` (Step 1) — immediately on startup
  and then every 30 minutes, independently of MCP requests. The dashboard's "Refresh now" button
  (Step 5) can wake it early, so the interval only has to bound staleness for users who never
  click it.
- This is a deliberately simpler replacement for a litestream-over-SFTP-from-VM2 design considered
  earlier: the ledger is small (currently ~1 MB; item-level receipt scanning grows it an estimated
  2–3 MB/year — see Step 1's sizing note) and stays small for years, so a periodic full-snapshot
  download costs about the same as an incremental WAL pull would, with none of the extra moving
  parts. It also needs nothing beyond what a laptop already has to use the app at all — the same
  Cloudflare Access / Tailscale perimeter the PWA already crosses — whereas the SFTP design would
  have meant minting and managing a `litestream` binary, an SSH key, and `.deploy/.env` secrets
  *per laptop*. That distinction matters because `dinary-ai` is meant to run on more than one
  family member's machine against the same shared ledger (Step 1).
- MCP tool handlers query the local replica directly (zero added latency) by resolving the path
  via `get_db_path()`. If the daemon has not yet produced a local DB, they return a FastMCP error
  immediately. The dashboard surfaces refresh status (last-sync time, manual refresh) on every
  load — see Step 5.
- `inv analytics` checks if `dinary-ai` is reachable; if not, runs `setup-dinary-ai` idempotently
  before opening Marimo.
- Cross-platform: macOS (launchd) and Windows (Task Scheduler).

---

## Step 1 — Snapshot endpoint and background refresh daemon

### Server: `GET /api/analytics/db-snapshot`

Add to `src/dinary/api/analytics.py` (the same router that already serves `GET /api/analytics/summary`) a read-only route that hands out a consistent point-in-time copy of the live ledger:

`get_db_snapshot() -> FileResponse`:
- Defined as a plain `def`, not `async def` — Starlette runs sync route handlers in its threadpool automatically, so the blocking backup I/O below never touches the event loop; no explicit `run_in_threadpool` call is needed.
- Opens a fresh read connection via `get_connection()` (`dinary.db.storage`), creates `tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)`, closes it immediately (only its path, `tmp_path = Path(tmp.name)`, is needed — the file must not be open when SQLite writes to it), opens `target = sqlite3.connect(tmp_path)`, and calls `source.backup(target)` — Python's wrapper around SQLite's Online Backup API. This produces a fully consistent standalone copy of the live database without holding any lock for longer than a single internal page-copy step; the API is explicitly designed to run concurrently with WAL-mode writers (`dinary/db/storage.py` already sets `journal_mode=WAL`). Closes `target` then `source` in `finally` blocks, in that order.
- Returns `FileResponse(tmp_path, media_type="application/octet-stream", filename="dinary-snapshot.db", background=BackgroundTask(lambda: tmp_path.unlink(missing_ok=True)))`. Starlette runs the `BackgroundTask` only after the response body has been fully streamed to the client, so the temp file is cleaned up whether the download completed or the client disconnected early.
- Add to `analytics.py`'s imports: `import sqlite3` (already present), `import tempfile`, `from fastapi.responses import FileResponse`, `from starlette.background import BackgroundTask`, `from dinary.db.storage import get_connection`.

No new authorization is added: this route is read-only and sits behind the same network perimeter (Cloudflare Access / Tailscale — see `specs/reference/architecture.md`'s "Single user. No in-app auth layer") that already fronts every route in this app, including ones that *mutate* the ledger with no token check at all. A read-only snapshot is strictly less sensitive than those — adding a bespoke auth scheme here would be new surface for no corresponding safety gain, and the architecture deliberately keeps the perimeter, not in-app auth, as the boundary.

**Sizing**: the live `dinary.db` is currently ~1 MB across `expenses` (~4 000 rows), `receipts`, `receipt_items`, and their indexes. Each scanned receipt line item adds one `receipt_items` row at ingestion (`db/receipts.py`, carrying `name_raw`/`name_normalized`/`tax_label` text columns) and one `expenses` row at classification time (`persist.py:_write_single_item`); an actively-scanning household adds roughly 3 500–4 000 such item-pairs/year — an estimated 2–3 MB/year of growth, with `receipt_items` rows running larger than `expenses` rows because of those text columns. At that rate the ledger stays in the tens-of-MB range for years; a full-snapshot download every 30 minutes (or on demand) costs a few seconds of bandwidth at most, comparable to an incremental WAL pull, so there is nothing to gain from chasing incrementality here.

Tests, alongside the existing `get_analytics_summary` tests:
- `test_db_snapshot_returns_valid_sqlite_file` — the response body, written to a temp path, opens with `sqlite3.connect` and reports the same `expenses` row count as the live DB
- `test_db_snapshot_cleans_up_temp_file` — the server-side temp file no longer exists once the response has been fully consumed
- `test_db_snapshot_consistent_under_concurrent_write` — a write committed during the request either is or isn't present in the snapshot (both acceptable); the assertion is that the snapshot opens cleanly and is never torn

### Client: background refresh daemon

Create new file `src/dinary_analytics/exceptions.py` and define `RefreshError`.

Redefine `REPLICA_PATH` in `src/dinary_analytics/paths.py` to be platform-specific — it stops being server-relative (`_DATA_DIR / "ledger-replica.db"`), because `dinary-ai` runs on the user's machine, independent of the repo and of `settings.data_path`:
- macOS: `~/Library/Application Support/dinary/dinary-ai.db`
- Windows: `Path(os.environ["LOCALAPPDATA"]) / "dinary" / "dinary-ai.db"` — wrap missing key in `RuntimeError("LOCALAPPDATA not set; cannot determine DB path on Windows")`

This is the only path consumers need to know about: `open_ledger()`'s existing `replica_path or REPLICA_PATH` default now resolves to the daemon-refreshed file, so every existing call site — dashboard cells, `tags.py`, `events.py`, `views.py`, and the MCP `query` handler — keeps working unchanged (Step 2 covers the readiness gate that MCP handlers add on top). `sync_replica()` in `connection.py` has no production caller today — only `tests/analytics/test_connection.py` exercises it — so delete it and those tests outright. (`tasks/analytics.py` has its own, differently-implemented `_sync_replica()` that captures the DB over SSH; it is unrelated to `connection.py`'s function — neither calls the other — and is itself already unused by the current `analytics` task body. Step 4 removes it as part of retiring the SSH-sync approach this plan replaces.)

`refresh_replica() -> Path`:
- `db_path = REPLICA_PATH` (platform-specific path defined above). This is both the refresh target and the return value.
- Resolves `.deploy/.env` relative to the repo root, not CWD: `_REPO_ROOT = Path(__file__).resolve().parents[2]`, `_ENV_PATH = _REPO_ROOT / ".deploy" / ".env"`. This mirrors the anchoring `dinary.config` already uses for the same reason — "so cron, systemd, and interactive `uv run` all agree regardless of CWD" — which matters here because launchd/Task Scheduler start the daemon with an unpredictable working directory. Reads `DINARY_APP_URL` (e.g. `https://dinary-host.tailxxxx.ts.net` — the same Tailscale-served origin the PWA already crosses) via `dotenv_values(_ENV_PATH)`; raises `RefreshError("DINARY_APP_URL not set in .deploy/.env")` if absent or empty. New optional variable — document it in `.deploy.example/.env` next to `DINARY_REPLICA_HOST`, with a comment that it is the base URL `dinary-ai` downloads snapshots from and must be reachable from the laptop (Tailscale MagicDNS / `tailscale serve` origin, not the bare `user@host` of `DINARY_DEPLOY_HOST`).
- Downloads via `urllib.request.urlopen(f"{DINARY_APP_URL}/api/analytics/db-snapshot", timeout=60)` — the generous timeout leaves headroom as the snapshot grows over the years (currently well under 1 MB; see the sizing note above).
- Streams the response body in chunks to a sibling temp path, `tmp_path = db_path.with_suffix(".tmp")`, then `os.replace(tmp_path, db_path)` — atomic on both POSIX and Windows, so a reader (DuckDB via `open_ledger()`) opening `db_path` mid-download always sees either the previous complete file or the new complete one, never a partial write.
- Returns `db_path`.
- Raises `RefreshError` on `urllib.error.URLError`, `OSError`, or a non-200 response status; caller logs a warning and keeps serving the stale local copy.
- Add to top-level imports: `import os`, `import urllib.error`, `import urllib.request`; `from dotenv import dotenv_values`.

**Why a plain HTTP download instead of litestream-over-SFTP-from-VM2** (the design considered and rejected): that design would have required `dinary-ai` to ship a `litestream` binary and generate and manage a per-laptop SSH key (its path read from `.deploy/.env`, alongside the existing `DINARY_REPLICA_HOST`) — secrets that, critically, would need to be *minted and handed to* every machine that runs `dinary-ai`. That is a reasonable ask of the repo owner's own laptop, but `dinary-ai` is meant to run on more than one family member's machine against the same shared ledger — and asking a non-developer user to generate an SSH key and join a Tailscale tailnet is not realistic. A plain HTTP download needs nothing a laptop doesn't already have to use the PWA at all: the same Cloudflare Access / Tailscale perimeter, plus one URL. Combined with the sizing note above — the ledger is small enough that a full download costs about what an incremental pull would — the simpler mechanism wins outright, with no functional trade-off.

All of the following lives in `src/dinary_analytics/refresh.py` (same file as `refresh_replica()`).

Imports at top level: `import datetime`, `import threading`, `import time`.

Constants:
```python
REFRESH_INTERVAL_SECONDS = 1800  # 30 minutes — normal poll interval after success; manual "Refresh now" (Step 2/5) wakes the loop early, so this only needs to bound staleness for users who never click it
REFRESH_RETRY_BASE_SECONDS = 30  # first retry after error; doubles each failure, capped at REFRESH_INTERVAL_SECONDS
```

Module-level state (`_db_path`, `_last_refresh`, `_last_refresh_error` mutated only inside `_refresh_loop`; `_daemon_thread` mutated only in `start_refresh_daemon()`; `_wake_event` set from `trigger_refresh_now()` and cleared inside `_refresh_loop`; all read only via accessors):
```python
_lock: threading.Lock = threading.Lock()
_db_path: Path | None = None        # None = no successful refresh yet
_last_refresh: float | None = None  # time.time() timestamp
_last_refresh_error: str | None = None
_daemon_thread: threading.Thread | None = None
_wake_event: threading.Event = threading.Event()  # set by trigger_refresh_now() to cut the current wait short
```

`_refresh_loop() -> None`:
- Declares `global _db_path, _last_refresh, _last_refresh_error` at function top.
- Tracks a local `retry_delay: int` initialised to `REFRESH_RETRY_BASE_SECONDS`.
- Runs forever: calls `refresh_replica()`.
  - **Success**: acquires `_lock` to update `_db_path`, `_last_refresh`, and set `_last_refresh_error = None` atomically; resets `retry_delay = REFRESH_RETRY_BASE_SECONDS`; waits `_wake_event.wait(timeout=REFRESH_INTERVAL_SECONDS)` then `_wake_event.clear()`.
  - **`RefreshError`**: logs warning; acquires `_lock` to set `_last_refresh_error` and leave `_db_path` unchanged (stale or `None`); waits `_wake_event.wait(timeout=retry_delay)` then `_wake_event.clear()`; then doubles `retry_delay` capped at `REFRESH_INTERVAL_SECONDS`.
- Both branches wait via `_wake_event` rather than `time.sleep` so `trigger_refresh_now()` can cut either one short — the loop doesn't care whether `wait()` returned because of the timeout or because the event was set; either way it proceeds straight to the next `refresh_replica()` call. Always `clear()` immediately after waking, so a stale "set" from a previous trigger can't cause a busy-loop.

`trigger_refresh_now() -> None`:
- Sets `_wake_event`. Idempotent and safe to call while a refresh is already running in `refresh_replica()`: the event is only consulted (and cleared) inside the wait, so a `set()` that arrives mid-refresh simply makes the *next* wait return immediately — i.e. "refresh again right away once this one finishes," which is the desired behaviour for a user mashing the refresh button, not a bug to guard against.

`get_db_path() -> Path | None`:
- Acquires `_lock` and returns `_db_path`. Used by MCP tool handlers and `/health` — avoids capturing `None` at import time.

`get_last_refresh() -> float | None`:
- Acquires `_lock` and returns `_last_refresh`.

`get_last_refresh_error() -> str | None`:
- Acquires `_lock` and returns `_last_refresh_error`.

`start_refresh_daemon() -> None`:
- Declares `global _daemon_thread` at function top.
- Guard: if `_daemon_thread is not None and _daemon_thread.is_alive()`, returns immediately — prevents two concurrent refresh loops if called twice.
- Spawns `threading.Thread(target=_refresh_loop, daemon=True)`, assigns to `_daemon_thread`, and starts it. The first refresh runs immediately (before the first wait).

MCP tool handlers call `get_db_path()`; no refresh is triggered inside a handler.

Tests in `tests/analytics/test_refresh.py`:
- `test_refresh_replica_returns_path` — returns `Path` on a successful download
- `test_refresh_replica_raises_on_failure` — raises `RefreshError` on `URLError` / non-200 status
- `test_refresh_replica_writes_atomically` — patch `urlopen` to return a slow/chunked body and assert `db_path` is only ever observable as the previous complete file or the new complete one (`os.replace`, never a partial write)
- `test_refresh_loop_sets_db_path` — after one iteration `_db_path` is set to the returned path
- `test_refresh_loop_logs_on_error` — `RefreshError` on first iteration logged; `_db_path` stays `None`; loop continues
- `test_refresh_loop_keeps_stale_path_on_error` — after a successful refresh followed by `RefreshError`, `get_db_path()` returns the previous (stale) path, not `None`
- `test_refresh_loop_retries_after_failure` — loop calls `refresh_replica()` again after a failed attempt
- `test_refresh_loop_backoff_doubles_on_repeated_errors` — wait intervals double on consecutive `RefreshError`s (30 → 60 → 120 …), capped at `REFRESH_INTERVAL_SECONDS`
- `test_refresh_loop_resets_delay_after_success` — after a sequence of errors followed by a success, the next wait is `REFRESH_INTERVAL_SECONDS`, not the accumulated backoff value
- `test_trigger_refresh_now_wakes_loop_immediately` — calling `trigger_refresh_now()` while `_refresh_loop` is waiting (either the normal interval or a retry backoff) causes the next `refresh_replica()` call without waiting out the full timeout
- `test_start_refresh_daemon_spawns_daemon_thread` — `start_refresh_daemon()` creates a thread with `daemon=True` and starts it
- `test_get_last_refresh_returns_timestamp` — after a successful refresh, `get_last_refresh()` returns the `time.time()` value set during that refresh
- `test_get_last_refresh_error_returns_message` — after a `RefreshError`, `get_last_refresh_error()` returns the error string; after a subsequent success it returns `None`

---

## Step 2 — `src/dinary_analytics/ai_service.py`

`MCP_PORT: int = 8765` lives in `src/dinary_analytics/paths.py` — the single source of truth imported by `ai_service.py`, `tasks/analytics.py`, and `notebooks/dashboard.py`. It belongs in `paths.py` rather than in `ai_service.py` itself: `tasks/analytics.py` is imported by `tasks/__init__.py` on *every* `inv` invocation, and importing `ai_service` to read a port number would execute its full module body — constructing `FastMCP`, registering every `@mcp.tool()`, and pulling in `duckdb` / `mcp` / `starlette` / `uvicorn` through `connection.py` — turning `inv pre`, `inv dev`, and every other task into one that loads the entire MCP+analytics stack. `paths.py` is already documented as having no heavy deps and being safe to import anywhere.

Replaces `mcp_server.py` as the primary entry point. Delete `mcp_server.py`; rename `tests/analytics/test_mcp_server.py` to `tests/analytics/test_ai_service.py` (if it exists) and update imports to `dinary_analytics.ai_service`. Retain all MCP tools from `mcp_server.py` — only the module structure, replica-path resolution, and startup sequence change.

```
startup:
  1. start_refresh_daemon()   # spawns background thread; first refresh runs immediately
  2. start FastMCP on --port (default 8765)

shutdown (SIGTERM):
  3. stop FastMCP
```

MCP tool handlers do not trigger any refresh. `_run_query` (and any future ledger-reading tool) calls `get_db_path()` from `dinary_analytics.refresh` first; if `None`, returns a FastMCP error immediately (replica not yet available) — *before* touching `open_ledger`. When `get_db_path()` returns a `Path`, the handler passes it explicitly to `open_ledger(path)`. (The zero-arg `open_ledger()` still works for notebooks and other non-MCP callers — its `REPLICA_PATH` default now resolves to the same daemon-refreshed file, see Step 1 — but MCP handlers go through `get_db_path()` because only it carries the "has a refresh ever succeeded" signal they need to gate on.) Do not let `sqlite3` raise an unhandled exception to the client.

`GET /health` is registered via `@mcp.custom_route("/health", methods=["GET"])`. The handler is `async`, accepts a `starlette.requests.Request`, and returns a `starlette.responses.JSONResponse`. Add `import argparse`, `import datetime`, `from starlette.requests import Request`, and `from starlette.responses import JSONResponse` to module-level imports. The handler calls `get_db_path()`, `get_last_refresh()`, and `get_last_refresh_error()` from `dinary_analytics.refresh`. Returns `{"ok": bool, "last_refresh": "ISO8601" | null, "error": "..." | null}`. `ok` is `true` when `get_db_path() is not None` (replica available and queries can be served, even if data is stale); `false` when no refresh has ever succeeded and the service cannot serve any data. Clients check `ok` for query-ability; `error` is diagnostic only and does not flip `ok` to `false`. `last_refresh` is `null` when no refresh has completed; otherwise `datetime.datetime.fromtimestamp(get_last_refresh(), tz=datetime.timezone.utc).isoformat()`.

`POST /refresh/now` is registered via `@mcp.custom_route("/refresh/now", methods=["POST"])` — the "Refresh now" button in the dashboard (Step 5) calls it to force an immediate refresh instead of waiting for the next scheduled poll (now `REFRESH_INTERVAL_SECONDS = 1800`, Step 1). The handler is `async`, calls `trigger_refresh_now()` from `dinary_analytics.refresh`, and returns `JSONResponse({"triggered": True})` immediately — it does not wait for the refresh to finish. The caller polls `GET /health` for the updated `last_refresh`.

`main()` is defined in `ai_service.py`:
```python
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=MCP_PORT)
    args = parser.parse_args()
    start_refresh_daemon()
    mcp.settings.host = "127.0.0.1"
    mcp.settings.port = args.port
    mcp.run(transport="streamable-http")
```

`ai_service.py` must end with:
```python
if __name__ == "__main__":
    main()
```
so that `python -m dinary_analytics.ai_service` works (required by the launchd plist and Task Scheduler XML in Step 3).

`_ensure_dinary_ai` (Step 4) waits only for HTTP reachability, not replica readiness — the service responds to `/health` before the first refresh completes. If `get_db_path()` is still `None` when Marimo opens, the notebook's top-cell health check (Step 5) shows the "replica not ready" error and halts rendering. No special handling is needed in `_ensure_dinary_ai`.

Tests in `tests/analytics/test_ai_service.py`:
- `test_startup_refresh_failure_still_starts` — `RefreshError` on startup is logged; service starts with `get_db_path() == None`
- `test_tool_handler_returns_error_when_no_db` — handler returns FastMCP error when `get_db_path()` returns `None`
- `test_health_ok` — returns `{"ok": true}` after successful refresh
- `test_health_degraded_no_db` — returns `{"ok": false}` when no refresh has ever succeeded
- `test_health_ok_with_stale_data` — returns `{"ok": true, "error": "..."}` when `_db_path` is set but last refresh failed
- `test_refresh_now_triggers_loop` — `POST /refresh/now` calls `trigger_refresh_now()` and returns `{"triggered": true}` without blocking for the refresh to finish

---

## Step 3 — `tasks/dinary_ai.py`

- `inv setup-dinary-ai` — idempotent: calls `install-dinary-ai` if the plist/task does not already exist (macOS: check plist file presence; Windows: `schtasks /query /tn dinary-ai` exit code 0 = exists). Finally ensures the service is actually running:
  - macOS: `launchctl kickstart -k gui/$(id -u)/dev.dinary.ai` — works whether the agent is freshly loaded, loaded-but-stopped, or already running (`-k` restarts a running instance, so a freshly-written plist's changes take effect too).
  - Windows: `schtasks /query /tn dinary-ai /fo csv` to read the `Status` column; runs `schtasks /run /tn dinary-ai` only when it reads anything other than `Running`.

  Safe to call repeatedly.
- `inv install-dinary-ai` — writes launchd plist (macOS) or Task Scheduler XML (Windows), then activates: macOS calls `launchctl load ~/Library/LaunchAgents/dev.dinary.ai.plist`; Windows calls `schtasks /run /tn dinary-ai`.
- `inv uninstall-dinary-ai` — macOS: `launchctl unload <plist>` then deletes the plist file. Windows: `schtasks /delete /tn dinary-ai /f`.

launchd plist: `~/Library/LaunchAgents/dev.dinary.ai.plist`, `KeepAlive: true`, `RunAtLoad: true`, `WorkingDirectory: <repo_root>`. The working directory is required, not optional: launchd starts agents with `cwd=/`, and `uv run` resolves `pyproject.toml` and the project's venv relative to cwd — without `WorkingDirectory` the daemon fails to start. `<repo_root>` is computed the same way as elsewhere in `tasks/`: `Path(__file__).resolve().parents[1]` from `tasks/dinary_ai.py`.
`ProgramArguments`: `[<uv_path>, "run", "python", "-m", "dinary_analytics.ai_service", "--port", str(MCP_PORT)]` where `<uv_path>` is the absolute path resolved at install time via `shutil.which("uv")` (raises `RuntimeError` if not found). Using the absolute path is required because launchd runs agents with a restricted `PATH` that typically does not include `~/.local/bin` or Homebrew prefixes where `uv` lives. `MCP_PORT` is imported from `dinary_analytics.paths` at install time — do not hardcode `8765`.

Windows: Task Scheduler, trigger `AtLogon`, restart on failure 3×, `<WorkingDirectory>` set to `<repo_root>` (same value, same reasoning as the plist's `WorkingDirectory` — `uv run` needs cwd to resolve the project). The XML config is written to a `tempfile.NamedTemporaryFile(suffix=".xml", delete=False)`, passed to `schtasks /create /xml <tempfile>`, then deleted. The task definition is stored in the Windows Task Scheduler database — the XML file itself is not kept. Permanent task name: `dinary-ai`.

Tests in `tests/tasks/test_dinary_ai.py`:
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

`analytics` calls `_ensure_dinary_ai(c)` before opening Marimo. `pty=False` (default) — `setup-dinary-ai` is non-interactive. Replica-readiness is not checked here — if the replica is not yet ready when Marimo opens, the notebook's top-cell health check (Step 5) surfaces the error.

`analytics` task signature loses `--mcp-port`; keeps `--port` for Marimo only.

---

## Step 5 — Marimo notebook shows snapshot status and blocks on refresh error

Top cell of `src/dinary_analytics/notebooks/dashboard.py` becomes a visible **snapshot-status banner** that stays at the top of the dashboard: it still halts everything below it on error, but in the healthy case it renders something the user sees on every load — last-refresh time plus a manual refresh control — and lets the rest of the notebook render beneath it.

Detecting "the DB updated after I clicked refresh" must never block the kernel — Marimo's idiom for periodic, non-blocking re-execution is `mo.ui.refresh`, a UI element whose value changes on a timer and whose readers re-run automatically when it does. Declare, alongside the notebook's other top-level UI elements: `refresh_ticker = mo.ui.refresh(default_interval="5s")` and, alongside the other `mo.state` calls (e.g. near `draft_view`/`view_list_ver`): `refresh_requested, set_refresh_requested = mo.state(False)`.

The status cell reads both `refresh_ticker` and `refresh_requested` — so it re-runs every 5 seconds *and* immediately when the button below is clicked — and on each run:
1. Imports `MCP_PORT` from `dinary_analytics.paths`; reads `refresh_ticker.value` (the read creates the periodic dependency — the value itself is unused) and `refresh_requested()`.
2. If `refresh_requested()` is `True`: `POST`s to `http://localhost:{MCP_PORT}/refresh/now` (fire-and-forget — `trigger_refresh_now()` wakes the daemon and the route returns immediately, Step 2), then immediately calls `set_refresh_requested(False)` so the trigger fires once per click, not on every following tick.
3. Calls `GET http://localhost:{MCP_PORT}/health` via `urllib.request.urlopen` — the same client as `_ensure_dinary_ai`, one quick request, no waiting loop — and parses the body with `json.loads` (already imported in the notebook's top cell).
4. `urllib.error.URLError` (service not running) halts everything below: `mo.stop(True, mo.callout(mo.md("**dinary-ai not running** — run `inv analytics`"), kind="danger"))`.
5. `ok: false` in the parsed response (no refresh ever succeeded) also halts everything below: reads `error` from the same payload and calls `mo.stop(True, mo.callout(mo.md(f"**Replica not ready:** {error}"), kind="danger"))`.
6. Otherwise renders the banner and lets the notebook continue — e.g. `mo.hstack([mo.md(f"🔄 Snapshot refreshed **{_format_ago(last_refresh)}**"), mo.ui.button(label="Refresh now", on_click=lambda _: set_refresh_requested(True))])`. `_format_ago` is a small helper near the notebook's other formatting helpers that turns the ISO 8601 `last_refresh` into "just now" / "5 minutes ago" / "2 hours ago" relative to `datetime.datetime.now(tz=datetime.timezone.utc)`.

End to end: clicking "Refresh now" sets `refresh_requested`, the cell re-runs immediately, fires the trigger, and renders the *previous* `last_refresh` (the daemon hasn't refreshed yet); `refresh_ticker` then re-runs the same cell every ~5 seconds, and as soon as one of those runs observes a new `last_refresh`, the banner updates on its own. No cell ever waits on the refresh — the periodic re-run **is** the background check.

Once this gate passes for the first time, `get_db_path()` is guaranteed non-`None` for the rest of the notebook's lifetime — `_db_path` is only ever replaced with a fresher path or left stale, never reset to `None` (Step 1). Cell bodies elsewhere in the dashboard need no changes for this step: their existing zero-arg `open_ledger()` / `load_view_frame(...)` calls resolve `REPLICA_PATH` to the daemon-refreshed file (Step 1).

In the normal flow `_ensure_dinary_ai` (Step 4) guarantees the service is running before Marimo opens; the `URLError` branch is a safety net for notebooks opened manually.

Because the user can now force a sync on demand, the background poll only has to bound staleness for people who never click the button — hence `REFRESH_INTERVAL_SECONDS` moves from 10 minutes to 30 (`1800`, Step 1).

---

## Step 6 — Update `specs/reference/analytics-ai.md`

The spec currently documents the architecture this plan replaces: `mcp_server.py` as the entry point, `ledger-replica.db` synced into `.analytics/` "on every `inv analytics` run", and the flow "1. Sync replica → 2. Start MCP server → 3. Open dashboard". Update it to describe the new architecture as current state only (no before/after — see spec conventions):
- Package structure: `mcp_server.py` → `ai_service.py`
- Storage: the replica lives at the platform-specific path the background daemon refreshes into (Step 1) by periodically downloading a consistent snapshot over HTTP from the dinary server, independently of `inv analytics` on its own schedule (and on demand via the dashboard's refresh control, Step 5) — not synced once per run into `.analytics/`
- `## inv analytics`: ensures `dinary-ai` is reachable (auto-installing it via `setup-dinary-ai` when it isn't) and opens the dashboard; refreshing the replica is the daemon's responsibility, not a step of this flow
- `## MCP server`: tool list is unchanged — just confirm the section no longer names `mcp_server.py`

---

## Done gate

- `uv run inv pre` → 0 errors
- `uv run pytest` → 0 failures
- Manual: `inv analytics` on clean machine auto-runs `setup-dinary-ai`, `dinary-ai` starts, MCP reachable, Marimo opens.
