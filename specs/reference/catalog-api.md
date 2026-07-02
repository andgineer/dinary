# Catalog API — Architecture

## Snapshot shape

`GET /api/catalog` returns every row — active and inactive — for each entity
type, each carrying an `is_active` flag. The PWA filters client-side and
exposes a per-picker "show inactive" toggle rather than the server omitting
retired rows.

## Version-gated writes

Catalog mutations only bump `catalog_version` when the observable content
actually changes (hash gate). This prevents no-op rewrites (e.g. re-seeding an
unchanged config) from invalidating PWA caches.

Both write paths — direct API mutations and the seed/import path — share the
same hash function so "observable change" has one definition across the system.

## FK-safe sync

The catalog is never wiped and re-inserted during sync. Ledger tables hold real
foreign keys into catalog rows, so deleting or renumbering a row would violate
referential integrity. Instead, sync marks all rows inactive then upserts by
name (natural key): matched rows are restored with their existing integer ID; new
rows get the next ID; unmatched rows stay inactive but remain FK-valid.

## Retirement and idempotent replay

A category/tag retired after an expense was posted against it stays valid for
that expense's idempotent replay (same `client_expense_id` and body) — the
stored ledger row proves it was live when the original request was sent. A
genuinely new POST against the retired value, or a replay with a different
body, is rejected normally (422 new / 409 conflict): retirement never relaxes
the conflict check itself.

## Soft vs hard delete

Delete auto-degrades to soft-deactivation when a row is still referenced by
expenses or mapping tables. The response reports which mode happened so the UI
can distinguish "gone" from "hidden but still used in history".

Group deletion is intentionally stricter — it rejects the request if any child
category still points at the group, active or not. Silently orphaning categories
would break the category hierarchy for all expenses that reference them.

## ETag caching

`catalog_version` is the ETag. Clients send `If-None-Match` and get 304 on a
cache hit, avoiding full payload retransmission on every PWA refresh.

The response includes a `removable` flag per item so the UI can decide whether
to show a delete button before the user attempts it — avoids a round-trip error.

## Auto-tags on events

Event auto-tags are stored as tag IDs. At expense-insert time the stored IDs
are resolved to live tag records. Tag deactivation (hiding from the picker)
does not block auto-attach — retired tags still apply to events that reference
them. A tag referenced only via `events.auto_tags` (zero ledger usage) still
triggers soft-delete rather than a hard delete.

## Frequent categories

Manual-expense categories dominate the quick-pick list; LLM-classified items are
excluded. If LLM choices counted, the quick-pick would converge on whatever the
receipt pipeline classifies most often rather than reflecting the user's
deliberate manual choices.

## Expense and receipt deletion

Manual expenses and receipt-backed expenses follow different deletion paths.
Manual expense DELETE removes a single row. Receipt-backed expense DELETE is
rejected (409) — those expenses must be deleted through their receipt.

Receipt DELETE cascades server-side, atomically removing the receipt row and
every expense derived from it. Cascading in the server (not the client) ensures
no orphaned expense rows can exist without their receipt, regardless of client
failures or partial retries.

Receipt-backed expense amounts are not editable through the expense PATCH
endpoint. The receipt is the source of truth for item amounts; allowing edits
out of receipt context would create inconsistency between stored receipt items
and the derived expenses that reference them.

## Authentication

No auth on admin endpoints. Deployments must sit behind a private network or
reverse-proxy ACL.
