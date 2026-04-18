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
