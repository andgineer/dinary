# Currencies API

## Design: PWA vs server responsibilities

The PWA owns its picker state — which currency codes the operator keeps as
quick-pick chips, which one they last selected, and so on. **It does not need
rates.** The server is the source of truth for exchange-rate conversion, which
happens at write time inside `POST /api/expenses` (the audit tuple
`(amount_original, currency_original)` is stored verbatim and the
NBS-anchored conversion to `settings.accounting_currency` is computed and
written there).

Therefore the PWA-facing surface is intentionally limited to the saved-codes
CRUD; no rate endpoints exist. See
[`exchange-rates.md`](exchange-rates.md) for the rate sources and
conversion logic.

## Endpoints

| Method | Path | Body | Description |
|--------|------|------|-------------|
| `GET` | `/api/currencies` | — | Saved currency codes |
| `POST` | `/api/currencies` | `{code: "ABC"}` | Add code (idempotent) |
| `DELETE` | `/api/currencies/{code}` | — | Remove code |

## Authentication

Currently inherited from the rest of the admin surface: deferred to the future
auth pass. Deployments are expected to put the service behind a private network
or ACL.
