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
