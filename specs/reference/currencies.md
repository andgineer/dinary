# Currencies and Exchange Rates

## Responsibility split

The PWA owns its picker state — which currency codes the operator keeps as
quick-pick chips, which one was last selected, etc. The server is the source of
truth for exchange-rate conversion.

Conversion happens at write time: when an expense is created, the server
converts the original amount to the accounting currency using the NBS-anchored
rate for that date and stores both the original pair and the converted amount.
The PWA never needs rates and no rate endpoints are exposed to it.

This design keeps the audit trail complete: the original amount and currency are
preserved verbatim, and the accounting-currency amount is derived deterministically
from the official rate at the time of purchase.

## Two-source rate strategy

Two upstream sources are used with overlapping coverage:

- **NBS** (`kurs.resenje.org`): RSD-anchored, ~40 currencies, daily on working days.
- **NBP** (`api.nbp.pl`): PLN-anchored, 148 currencies (full ECB roster plus
  regional), daily for majors, weekly for less-common currencies.

NBP covers every currency NBS covers plus many more, so the fallback can serve
any pair the primary can.

Resolution policy:
- Pairs containing RSD: NBS direct first, fall back to NBP bridged through PLN.
- Pairs without RSD: bridge through RSD via NBS, fall back to bridge through PLN via NBP.

Both legs of a pair are stored together so future lookups can resolve either
direction without recomputing.

## Stale-rate tolerance

NBP Table B (less-common currencies) is published only on Wednesdays. A date
lookup outside the publication schedule returns 404; the client falls back to
the most recently published rate. Up to six days of staleness is accepted for
these currencies — they are rare enough that the approximation is not
operationally significant.

## Accounting currency source of truth

`app_metadata.accounting_currency` is the runtime source of truth; the
`DINARY_ACCOUNTING_CURRENCY` env var only seeds it on first deploy against a
fresh database. Once seeded, changing the env var without updating the stored
value raises rather than silently switching currencies (typo-guard) — the
operator must go through an explicit migration to change the accounting
currency later.

## Authentication

Currently deferred along with the rest of the admin surface. Deployments are
expected to put the service behind a private network or ACL.
