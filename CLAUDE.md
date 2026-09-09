# Dinary — Codebase Guide

## Project overview

Expense-tracking app for Serbia: scan fiscal receipts via QR code, classify items with an LLM, and sync to Google Sheets. Consists of a FastAPI backend and a Vue 3 PWA frontend.

For architecture, system layout, technology decisions, data model, deployment, and configuration see [specs/reference/architecture.md](specs/reference/architecture.md).

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

---

## Environment setup

A bare `uv sync` is **not** enough to reach a green suite. The full test run and `inv pre` also need:

1. **All dependency groups**: `uv sync --all-groups` — the `analytics` group (duckdb, lmdb, marimo, mcp, altair, polars, google-genai) is required, or `tests/analytics/` fails to collect and pyrefly reports missing-import errors.
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
- Reply to the user in whichever language they used, in its native script.

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

### Tests
- Every new function or module needs tests in the same session. Never skip or defer.
- Python: `uv run pytest` from the repo root. Frontend: `npm test` from `webapp/`, one test file per component / composable / store, mirroring the source path.
- Verify with the full suite, never a narrow `pytest -k …` subset.

### Linting
- `inv pre` is the only gate. Never run ruff (or `uvx ruff` / `uv run ruff`) directly; never bypass hooks with `--no-verify`.
