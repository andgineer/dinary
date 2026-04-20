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

- **Always run `inv pre` and the full test suite (`uv run pytest`) before
  telling the user that changes are complete or that "lint is clean".**
  Running only `ReadLints` or a narrow `pytest -k` subset is **not** a
  substitute — `inv pre` runs ruff, ruff-format, and pyrefly with the
  project's actual configuration, and it is the gate that matters.
- If `inv pre` reports errors (even ones that appear pre-existing in
  unrelated files), fix them in the same change before reporting done.
  Do not dismiss lint errors as "out of scope" or "pre-existing" unless
  you have explicitly confirmed they fail on ``main`` as well *and* the
  user has agreed to defer them. Dismissing them silently is a lie to
  the user about the state of the tree.
- If `inv pre` modifies files (ruff-format, end-of-file-fixer, etc.),
  re-run it until it converges to "All checks passed!" / "0 errors".
