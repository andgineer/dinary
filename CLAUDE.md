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

## Non-negotiable done gate

Before claiming anything is done, both must be green:

1. `uv run inv pre` → "All checks passed!" + `0 errors` from pyrefly
2. `uv run pytest` → `N passed` with zero failures or errors

Run `inv pre` after each discrete batch of changes, not only at the end.

---

## Code conventions

These rules come from `AGENTS.md` and supplement the defaults in this file.

### Language
- All comments, docstrings, plan files, and in-repo docs: **English only**.
- Data literals (category names, sheet headers, envelope names like `"командировка"`) stay in their **original script** (Cyrillic, etc.) — do not transliterate.
- Reply to the user in whichever language they used.

### Imports
- **No local (in-function) imports.** All imports at module top level, always.
- **No `from __future__ import annotations`** — the project targets Python 3.13+.
- **No re-export patterns** — callers import directly from the module that owns the symbol.

### Plan files
- Never reference `.plans/*.md` files or step numbers from plans inside code comments or docstrings. Plans are ephemeral; the code is the source of truth.

### Spec files
- Specs in `specs/` capture architectural decisions and business requirements only — not implementation details.
- Correct spec content: "every expense created from a receipt must have a matching rule", "retry every 15 minutes on day 1, then once a day indefinitely."
- Never put function signatures, argument lists, field names, or internal class structure in specs. The code is the source of truth for those.
- Specs describe **current state only**. Motivation, experiments, and rationale are welcome. Never track implementation changes ("previously X, now Y", "approach Z was removed") — state only the current rule. Git history records the evolution.

### Tests
- Every new function needs tests in the same session. Never skip.

### Linting
- `inv pre` is the only gate. Never run ruff directly; never bypass hooks with `--no-verify`.
