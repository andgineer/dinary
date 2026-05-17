# Catalog API

## Write path

Catalog mutations flow through four focused modules (`catalog_writer_groups`,
`catalog_writer_categories`, `catalog_writer_events`, `catalog_writer`) plus
shared types in `catalog_writer_errors`. Every mutation:

1. Opens `BEGIN IMMEDIATE`.
2. Snapshots `_hash_state` (sha256 over all four catalog tables ordered by id).
3. Applies the change.
4. Calls `_commit_with_bump`: re-hashes; bumps `catalog_version` only when
   the hash changed.

This means no-op rewrites (re-adding an already-active row) never bump the
version and never invalidate PWA caches.

The seed path (`imports.seed.rebuild_config_from_sheets`) uses the same
`hash_catalog_state` function for its own bump gate, so both write paths
share one definition of "observable catalog change".

## FK-safe sync (`seed_config`)

`seed_classification_catalog` never deletes or renumbers rows. Ledger tables
carry real FKs into the catalog; removing a referenced row would violate them.

Algorithm: mark every catalog row `is_active=FALSE`, then upsert by name
(natural key). Matched rows get `is_active=TRUE` restored and keep their
existing integer `id`. New rows get `id = max(id)+1`. Unmatched rows stay
`is_active=FALSE` — hidden from the live API but still FK-valid.

## Soft vs hard delete

`delete_*` auto-degrades to soft-delete (`is_active=FALSE`) when the row is
still referenced by `expenses`, `expense_tags`, or any mapping table. The
`DeleteResult.status` field reports `"hard"` vs `"soft"` so the PWA can tell
the operator "gone for good" vs "still available under Show inactive".

`delete_group` is stricter: it raises `CatalogInUseError` (409) if any
category (active or inactive) still points at the group. Moving a group while
its categories remain attached would silently orphan those categories; the
operator must relocate or delete the children first.

## `GET /api/catalog` — ETag caching

ETag is `W/"catalog-v<N>"` derived from `catalog_version`. The body does not
echo the ETag — clients derive it from `catalog_version`. Sending
`If-None-Match` returns `304 Not Modified` on a cache hit.

`removable=true` on an item means a DELETE would hard-delete it (no references
anywhere). The PWA hides the "Удалить" button on non-removable rows.

All rows including `is_active=FALSE` are always returned; the PWA filters
client-side and exposes per-picker "show inactive" toggles.

## Auto-tags

`events.auto_tags` is a denormalised JSON array of tag **names** (not ids).
When `event_id` is attached to an expense (at insert time or via the drain
loop), those names are resolved to live tag ids and unioned into the expense's
`tag_ids`. This lets vacation events auto-tag every attached row with
`["отпуск", "путешествия"]` without the PWA replicating that logic.

`is_active` on a tag means "hide from the manual picker" — it does **not**
block auto-attach. Events must keep working against tags the operator has
retired from the picker.

## Authentication

No auth on admin endpoints yet. The previous `DINARY_ADMIN_API_TOKEN` gate was
removed because it was shared across every operator and the PWA stored it in
localStorage. A proper layer (OAuth / session / per-user key) will land with
multi-user support. Until then, deployments must sit behind a private network
or reverse-proxy ACL.
