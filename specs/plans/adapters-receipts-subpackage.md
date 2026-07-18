# Group the adapters cluster into subpackages

## Motivation

`src/dinary/adapters/` is a flat directory that already contains three distinct
clusters:

- **receipts** — `serbian_receipt_parser.py`, `montenegrin_receipt_parser.py`,
  `receipt_parsing.py` (dispatch), `receipt_types.py`
- **rates** — `exchange_rates.py`, `nbp.py`, `nbs.py`, `rate_helpers.py`
- **sheets** — `sheets_client.py`

The receipts cluster grew from one file to four when Montenegro support landed,
and the module names carry a redundant `_receipt_parser` / `receipt_` prefix that
a directory boundary would express better. Grouping the multi-file clusters into
subpackages makes the domains explicit, shortens the module names, and improves
discoverability. `adapters/` itself keeps its hexagonal-architecture meaning
(integrations with the outside world) — the change is internal grouping, not a
rename of `adapters`.

Packages here are namespace-style (no `__init__.py`), so a new subdirectory needs
no package boilerplate.

## Scope decision

- **Group `receipts/` and `rates/`** — both are multi-file clusters that read as
  their own domain.
- **Leave `sheets_client.py` flat** — a single module does not need a package;
  grouping one file adds a directory for no gain. (The higher-level Sheets logic
  already lives in the separate top-level `dinary/sheets/` package.)

The two moves are independent; do `receipts/` first (small blast radius, directly
motivated) and `rates/` second (larger importer surface).

The repo forbids re-export shims: every importer must be repointed at the new
module path. No compatibility aliases are left behind.

## Phase 1 — `adapters/receipts/`

### Move + rename

| From | To |
|---|---|
| `adapters/serbian_receipt_parser.py` | `adapters/receipts/serbian.py` |
| `adapters/montenegrin_receipt_parser.py` | `adapters/receipts/montenegrin.py` |
| `adapters/receipt_parsing.py` | `adapters/receipts/dispatch.py` |
| `adapters/receipt_types.py` | `adapters/receipts/types.py` |

Use `git mv` so history follows the files.

### Update importers (source)

- `adapters/receipts/dispatch.py` — imports `serbian`, `montenegrin`, `types`
  (now siblings).
- `adapters/receipts/serbian.py`, `adapters/receipts/montenegrin.py` — import
  from `.types` (siblings).
- `api/controllers/receipt_queue.py` — `receipt_parsing` → `receipts.dispatch`,
  `receipt_types` → `receipts.types`.
- `background/classification/task.py` — `receipt_parsing` → `receipts.dispatch`,
  `receipt_types` → `receipts.types`.
- `background/classification/persist.py` — `receipt_parsing` → `receipts.dispatch`.
- `db/receipts.py` — `receipt_types` → `receipts.types`.

### Update importers (tests) and monkeypatch paths

- Import lines in `tests/services/test_receipt_parser.py`,
  `tests/services/test_qr_payload.py`,
  `tests/services/test_montenegrin_receipt_parser.py`,
  `tests/services/test_receipt_parsing_dispatch.py`,
  `tests/tasks/test_receipt_pipeline.py`, `tests/tasks/test_receipt_drain.py`,
  `tests/api/test_receipt_pipeline_e2e.py`.
- **Monkeypatch string paths that name the source module must change:**
  - `dinary.adapters.serbian_receipt_parser.httpx.AsyncClient`
    → `dinary.adapters.receipts.serbian.httpx.AsyncClient`
    (12 occurrences in `test_receipt_parser.py`).
  - `dinary.adapters.montenegrin_receipt_parser.httpx.AsyncClient`
    → `dinary.adapters.receipts.montenegrin.httpx.AsyncClient`
    (2 occurrences in `test_montenegrin_receipt_parser.py`).
  - The dispatch test patches `receipt_parsing.serbian_receipt_parser` /
    `.montenegrin_receipt_parser` attributes via `patch.object` — repoint at the
    new `dispatch` module's imported names.

**Do not touch** patches of `dinary.background.classification.task.parse_receipt`
— that targets the name bound inside `task.py`, which is independent of where the
source module lives.

### Naming note

The modules do more than parse (they also fetch over HTTP and dispatch), so
`receipts/` is the right directory name, not `parsers/`. Inside it the files are
named by role: `serbian`, `montenegrin`, `dispatch`, `types`.

## Phase 2 — `adapters/rates/` (separate change)

### Move + rename

| From | To |
|---|---|
| `adapters/exchange_rates.py` | `adapters/rates/service.py` |
| `adapters/nbs.py` | `adapters/rates/nbs.py` |
| `adapters/nbp.py` | `adapters/rates/nbp.py` |
| `adapters/rate_helpers.py` | `adapters/rates/helpers.py` |

### Blast radius

Larger than Phase 1: ~24 importers across controllers, background tasks, and the
`tests/currency/` and `tests/api/` suites (grep `adapters.exchange_rates`,
`adapters.nbp`, `adapters.nbs`, `adapters.rate_helpers` for the full list). Repoint
each import; watch for any monkeypatch that names these modules by string path
(e.g. NBS/NBP HTTP clients in the currency tests). Same no-re-export rule.

Because the surface is wide and unrelated to any feature, keep Phase 2 as its own
commit/PR so review can focus on a mechanical rename.

## Done gate (each phase)

- `uv run inv pre` → "All checks passed!" + `0 errors` from pyrefly on touched code.
- `uv run pytest` → all green (pre-existing env-only failures excluded).
- `cd webapp && npm test` — unaffected by this backend-only move, but run once to
  confirm no accidental coupling.

Both phases are in scope. Do them as separate commits/PRs (Phase 1 first), and
delete this plan file once both are merged.
