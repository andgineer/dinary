# Dinary — Codebase Guide

## Project overview

Expense-tracking app for Serbia: scan fiscal receipts via QR code, classify items with an LLM, and sync to Google Sheets. Consists of a FastAPI backend and a Vue 3 PWA frontend.

For architecture, system layout, technology decisions, data model, deployment, and configuration see [specs/reference/architecture.md](specs/reference/architecture.md).

**Asked to install, update or move dinary, not to change it?** When the person you work for wants it installed on their own server — the Quick start's agent prompt — updated there, or moved to a new VM, follow `docs/includes/agent-install.md` instead. Everything below governs changes to the code and does not apply to those.

---

## Agent memory lives in this file

Everything an agent should remember across sessions — a correction from the maintainer, a project decision, a fact about the environment — is written into this file (`AGENTS.md` is a symlink to it) and committed in the same session. Never keep it only in an agent's local memory, such as Claude Code's `~/.claude/projects/*/memory/`: that is lost when the laptop is reinstalled, and other contributors and their agents never see it.

---

## Key commands

| Task | Command |
|---|---|
| Lint + type-check + format | `uv run inv pre` |
| Python tests | `uv run pytest` |
| Dev server (auto-reload) | `uv run inv dev` |
| Build Vue PWA into `_static/` | `uv run inv build-static` |
| Apply DB migrations (local) | `uv run inv migrate` |
| Frontend tests | `cd webapp && npm test` |
| List all tasks | `uv run inv --list` |

**Never call ruff directly.** Always use `inv pre` — it runs ruff, ruff-format, pyrefly, and pre-commit hygiene hooks in the correct order.

### Local ports

- The dev server's default port is 8000.
- Port 8765 belongs to the `dinary_analytics` MCP daemon (`MCP_PORT` in `src/dinary_analytics/paths.py`), which runs permanently on the maintainer's machine. Never start anything else on it.
- Before using any other port, check that it is free: `lsof -iTCP:<port> -sTCP:LISTEN`.
- `inv dev` also publishes its port with `tailscale serve` and runs `tailscale serve off` on exit. To look at a change in the browser, run `uv run uvicorn dinary.main:app --host 127.0.0.1 --port <port>` instead.

---

## Environment setup

A bare `uv sync` is **not** enough to reach a green suite. The full test run and `inv pre` also need:

1. **All dependency groups**: `uv sync --all-groups`, never a plain `uv sync`, which removes them again — the `analytics` group (duckdb, lmdb, marimo, mcp, altair, polars, google-genai) is required, or `tests/analytics/` fails to collect and pyrefly reports missing-import errors.
2. **`zstd` and `sqlite3` CLIs**: the backup/restore tasks and tests shell out to them (`apt-get install -y zstd sqlite3`).
3. **DuckDB `sqlite_scanner` extension**: DuckDB downloads it on the first `ATTACH (TYPE sqlite)`; uncached, that download can outlast the 60s pytest timeout and the analytics test dies with `RuntimeError: Query interrupted`.

All of it is wrapped in the tracked, idempotent script **`scripts/setup-test-env.sh`** — run it once on a fresh session (or whenever deps/binaries are missing) before anything else; never work around the gap by skipping tests.

`.claude/` is git-ignored, so a SessionStart hook can't be committed — this script is the tracked source of truth; point any local, untracked hook at it instead of duplicating the steps. In Claude Code on the web, set the environment's setup script (per-environment web setting, not in the repo) to `bash scripts/setup-test-env.sh`, and every session in that environment starts provisioned.

---

## Non-negotiable done gate

Before telling the user anything is "done", "fixed", "complete", "landed", "clean" or "ready", both must have been run in this order and seen fully green:

1. `uv run inv pre` → "All checks passed!" on every hook + `0 errors` from pyrefly
2. `uv run pytest` → `N passed` with zero failures, errors or unexpected passes (a known `xfail` is fine)

Run `inv pre` after each discrete batch of changes, not only at the end. If a hook modifies files (ruff-format, end-of-file-fixer, trailing-whitespace), re-run until it converges to "All checks passed!" — a "modified by hook" exit is not green, it is a pending fixup that must be committed.

No exceptions: not "docs-only change", not "the lint error is in an unrelated file", not a narrow `pytest -k <subset>`, not `ReadLints` (it misses ruff-format drift, pyrefly suppressions and hook-driven file rewrites), and not a green run from three turns ago — new edits invalidate it. Re-run both at the end of the turn.

Fix `inv pre` errors in the same change even when they look pre-existing or unrelated. The only valid deferral: confirm the error also fails on `main` **and** get the user to agree to defer it in this turn.

**Never leave a failing test.** Every session starts from green (main is green). There is no "pre-existing failing test" to ignore: red is either something you broke or a test that rotted — most often a **flaky or date-dependent** test (a hardcoded date aged out of a rolling `datetime('now', …)` window, an order-dependent assumption, a real clock/network dependency). Fix the root cause so it is deterministic — e.g. anchor dates relative to "now"; do not skip, `xfail`, delete or defer it, and do not hand back a red suite calling the failures unrelated. If the cause is a missing dependency or binary, fix the environment (see above), not the assertion.

---

## Code conventions

### Language
- All comments, docstrings, plan files, and in-repo docs (`docs/**/*.md`, `README.md`): **English only**. Exception: user-facing docs written for a Russian audience (e.g. `docs/src/ru/`) stay in Russian.
- Data literals cited in prose (category names, sheet headers, envelope names like `"командировка"`) stay in their **original script** and in quotes so they remain grep-able — do not transliterate or translate. String literals in code are data, not prose: this rule does not restrict them.
- Reply to the user in whichever language they used, in its native script. The maintainer (git user `andgineer`) wants every reply in Russian, even to a message written in English.
- Write replies and reports as plain technical prose: complete, connected sentences, kept short. Keep the technical terms and drop the padding. Avoid fragments without a verb, several thoughts glued together with dashes, a dash standing in for a reason that is never stated, and table cells of clipped fragments.

### Imports
- **No local (in-function) imports.** All imports at module top level, always — not for lazy loading, not to break a circular import; fix the dependency structure instead.
- **No `from __future__ import annotations`** — the project targets Python 3.13+.
- **No re-export patterns** — when a symbol moves, update importers to point at the new module instead of leaving a shim re-export. This does not forbid importing a shared helper; never duplicate logic across modules to "avoid" an import.

### Plan files
- **Location: `specs/plans/` — always.** Before creating a plan file, check the path starts with `specs/plans/`. Never `.plans/`, never the repo root, never any hidden or git-ignored directory. A plan outside `specs/plans/` is a bug: move it there in the same session.
- **Language: English — always.** Headings, tables, and prose included. A plan written while the conversation is in Russian (or any other language) is still written in English; only the reply to the user follows the user's language.
- A plan that is fully implemented and merged is deleted, not archived. Before deleting, move anything spec-worthy (architectural decisions, business requirements) into `specs/` — never implementation details.
- Never reference plan files or step numbers ("Step 14 of vue-refactor") inside code comments, docstrings, file headers or any other in-source prose. Plans are ephemeral; the code is the source of truth. If a comment needs rationale, restate it inline.

### Spec files
- Specs in `specs/` capture architectural decisions and business requirements only — not implementation details.
- Correct spec content: "every expense created from a receipt must have a matching rule", "retry every 15 minutes on day 1, then once a day indefinitely."
- Never put function signatures, argument lists, field names, or internal class structure in specs. The code is the source of truth for those.
- Specs describe **current state only**. Motivation, experiments, and rationale are welcome. Never track implementation changes ("previously X, now Y", "approach Z was removed") — state only the current rule. Git history records the evolution.
- **Specs must never link to plan files.** `specs/reference/` and `specs/ui/` may only link to other spec files.
- Never list API that does not exist ("there is no `X` shortcut") in specs or plans. Say what the thing is and what it takes. An absence may be stated only when the absence is itself the design decision, together with its reason.
- No one-line rationalisations next to a rule in specs, docs or code ("unlike X, Y does not…", "to avoid confusion…"). State the rule. A spec that needs real motivation gives it its own paragraph.

### Docstrings
- When cleaning up docstrings, judge each one by its content, never by its length. The bad ones are long multi-line essays and history narration; short one-liners are usually fine and stay. Never use a script that deletes docstrings by line count; review and edit file by file.

### Tests
- Every new function or module needs tests in the same session. Never skip or defer.
- Python: `uv run pytest` from the repo root. Frontend: `npm test` from `webapp/`, one test file per component / composable / store, mirroring the source path.
- Verify with the full suite, never a narrow `pytest -k …` subset.
- Tests for an `inv` task module's helpers go in `tests/tasks/test_tasks_<module>.py`.
- An `AggregateError` / `ECONNREFUSED` block in the `npm test` output means a real `fetch()` escaped the mocks. It is a bug to fix even when every test reports passed. `vi.spyOn(store, "fn")` does not intercept calls between functions inside the same Pinia setup store; mock the API module function instead.

### Linting
- `inv pre` is the only gate. Never run ruff (or `uvx ruff` / `uv run ruff`) directly; never bypass hooks with `--no-verify`.
- Never suppress complexity rules (`# noqa: PLR0915`, `PLR0913`, `C901` and the like). Fix the function: extract helpers, group parameters into a dataclass, split branches into named functions.
- `inv pre` runs pyrefly on all files, and there project-wide issues show only as warnings. The commit hook runs it on the staged files, and then the same issues can be reported as errors in files the commit never touched. When that blocks a commit, check that they fail on `main` too, then fix them with behavior-preserving static changes such as explicit annotations.

### llmbroker
- **Never work around an llmbroker problem in dinary.** When llmbroker is missing something dinary needs, or behaves wrongly, stop and discuss the llmbroker change with the user. The fix goes into llmbroker (a plan in its `specs/plans/`, implemented and released there); dinary adopts it by bumping the pin.
- No host-side patches in the meantime: no extra request parameters, bypassed context managers, re-implemented llmbroker logic, or suppressed llmbroker errors "until upstream is fixed".
- llmbroker issues are filed in `andgineer/llmbroker`, not in dinary.
- Call statistics over a time window come from llmbroker's `stats(since=…)`; dinary never computes them from raw journal records. dinary owns only the policy: the 7-day window, a 429 counts as a failure, and "no calls" is not "no failures".
- When changing llmbroker, never edit its version by hand (`src/llmbroker/__about__.py`). The maintainer bumps it with `invoke ver-*` at release time, so a missing bump is not a review finding.

---

## Git

- Contributors work on a branch and open a pull request.
- The maintainer (git user `andgineer`) is the exception: when working for the maintainer, never create pull requests or side branches. Commit on `main`, and run `git push origin main` when the maintainer asks for a push.

---

## Production and external services

- The production host is `DINARY_DEPLOY_HOST` and the replica is `DINARY_REPLICA_HOST`, both in `.deploy/.env`. The production database path is `_REMOTE_DB_PATH` in `tasks/devtools/constants.py`.
- `--prod` is the only flag that points an `inv` task at production (healthcheck, logs, status, verify-db, reports, sql, restores). Without it every task works on the local `data/dinary.db`, so a bare `inv healthcheck` says nothing about production.
- Reads with `--prod` run on a temporary snapshot on the server, which is why `sql --write --prod` is refused. Production restores ask for the literal word `prod`, and `--yes` never skips that prompt.
- For ad-hoc Google Sheets or Drive access, use the service account the app uses (`settings.google_sheets_credentials_path`, by default `~/.config/gspread/service_account.json`). Never ask the user to authenticate a Google MCP connector.

---

## Product decisions to keep

- The income form starts on the default currency, not the last-used one that the expense form starts on. Income is usually in the accounting currency, so a foreign currency carried over from an expense would mislead.
- A fresh database is seeded only with categories: the category vocabulary and the factory category templates. Tags and events start empty by design and grow as the user adds them.
- The LLM classification prompt always asks for 2–3 alternative categories. When alternatives were requested only below confidence 4, the model inflated its confidence to skip the extra work.
- The switchable preset of category groups and categories is a "category template" in code and specs and «набор категорий» in the Russian UI. It is never called "catalog" or "category set". Its levels are group and category.
