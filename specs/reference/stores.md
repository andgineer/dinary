# Stores and Shop Chains

## Location vs brand separation

Physical store locations and retail brands are separate entities. This separation
matters for classification rules: a rule learned at one Lidl branch (e.g.
"mleko" → Dairy) should apply at all Lidl branches. Storing rules at store
granularity would waste LLM calls and leave new branches uncovered until they
accumulated their own purchase history.

## PIB as the canonical store identifier

PIB (Serbian tax ID) is the canonical identifier for a store location. Every
fiscal receipt must carry a PIB; its absence is an error condition. Using PIB
rather than the store name string avoids duplicate records for the same location
when the raw name varies across receipts.

For stores without a PIB (an abnormal condition), a partial unique index on name
prevents duplicates — SQLite's TEXT UNIQUE allows multiple NULLs, so an explicit
partial index is required.

## Chain always set at store creation

Every store gets a chain assignment when it is first inserted. This guarantee
simplifies downstream queries: any join from expense → store → chain never needs
to NULL-guard the chain side. The column is technically nullable only to avoid a
circular foreign key dependency in the schema definition.

## LLM gets no existing chain list

When normalising a store name to a brand, the LLM is not shown the existing
chain list. Providing the list would anchor the model to existing entries and
prevent correct identification of genuinely new chains. Occasional inconsistency
(the same chain getting two slightly different names across receipts) produces
duplicate chain rows, but deduplication only requires updating the chain reference
on affected store rows — the analytical layer is not broken by duplicates.

## Deduplication is deferred

Duplicate chain normalisation is a known limitation and is intentionally not
addressed in the classification pipeline. A future maintenance task can merge
duplicate chains by updating store rows.
