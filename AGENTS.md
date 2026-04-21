# Agent rules

## Comments and docstrings

- Write all comments and docstrings in **English only**. No Russian (or any
  other non-English) prose in comments or docstrings — ever.
- When citing a concrete data value (a category name, source_type, envelope,
  beneficiary, event name, sheet column header, etc.), keep the value in its
  **original script** (Cyrillic, Japanese, …) and surround it with quotes so
  it stays grep-able. Do **not** transliterate or translate data literals.

  Bad:

  ```python
  # Pre-2022 envelope "komandirovka" marks real work trips.
  ```

  Good:

  ```python
  # Pre-2022 envelope "командировка" marks real work trips.
  ```

- String literals in code are data, not prose, so this rule does not restrict
  them. Keep `"командировка"`, `"гаджеты"`, etc. as-is.

## Imports

- **Never use local (in-function) imports.** All imports must be at module
  level. No exceptions — not for "lazy loading", not for "avoiding circular
  imports". If there is a circular import, fix the dependency structure.
- Do not use `from __future__ import annotations` — the project targets
  Python 3.13+.

## Plan files and in-repo docs

- All plan files (`.cursor/plans/*.md`, `.plans/*.md`) and in-repo
  documentation (`docs/**/*.md`, `README.md`, `AGENTS.md`, etc.) must be
  written in **English only**. No Russian prose in plans or docs.
- Exception: user-facing docs explicitly meant for a Russian audience
  (e.g. files under `docs/src/ru/`) stay in Russian.
- Data literals (category names, sheet headers, envelope names) keep their
  original script as in the Comments rule above.

## Communication with the user

- Reply to the user in the language they used (Russian or English), using
  proper native script. Do not transliterate Cyrillic into Latin letters.

## Verification before claiming done

**TL;DR — non-negotiable green gate.** Before you tell the user anything
is "done", "fixed", "complete", "landed", "clean", "ready", etc., you
MUST have run, in this order, and seen both go fully green:

1. `uv run inv pre` → "All checks passed!" on every hook, `0 errors`
   from pyrefly.
2. `uv run pytest` → `N passed` with **zero** failures, errors, or
   xpassed/unexpected results (known `xfail` is fine).

No exceptions. No "this change is docs-only so I skipped tests".
No "lint error is in an unrelated file so I left it". No running
only `ReadLints`, no narrow `pytest -k <subset>`, no trusting that a
passing `uv run pytest` from three turns ago still applies after new
edits. Re-run both, every time, at the end of the turn.

Details:

- `inv pre` runs ruff, ruff-format, pyrefly, and the pre-commit file
  hygiene hooks with the project's actual configuration — it is the
  only gate that matches CI. `ReadLints` does **not** substitute for
  it: ReadLints misses ruff-format drift, pyrefly suppressions, and
  hook-driven file rewrites (end-of-file-fixer, trailing-whitespace).
- If `inv pre` reports errors — *even ones that look pre-existing or
  unrelated to your change* — fix them in the same change before
  reporting done. The only valid way to defer a lint error is: (a)
  explicitly confirm it fails on `main` as well, and (b) get the user
  to agree to defer it in this turn. Silently dismissing errors as
  "out of scope" is lying to the user about the state of the tree.
- If `inv pre` modifies files (ruff-format, end-of-file-fixer,
  trailing-whitespace, etc.), re-run it until it converges to
  "All checks passed!" / `0 errors`. A "modified by hook" exit is
  not green — it is a pending fixup that must be committed.
- If `uv run pytest` reports a failure, fix it in the same change.
  A test that was broken by your edit is your responsibility to
  repair or to explicitly flag + get user agreement to defer.
  "329 passed" from a previous turn is not evidence; re-run.
