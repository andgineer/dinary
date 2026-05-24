# PWA Offline Architecture

## IndexedDB reconnect pattern

The offline queue never caches the database connection object across calls.
Instead it caches a Promise that resolves to the connection, and nulls that
Promise on `onclose` and `onversionchange` events. The next operation
re-opens cleanly by re-entering the open path.

Caching the connection object directly causes a "Database is disconnecting"
error when the browser closes the connection (e.g. on tab re-focus after a
long idle). That error froze the queue silently — no more expenses could be
submitted until the user reloaded. Caching the Promise instead of the
connection avoids this: a stale Promise is detected and replaced, not a stale
connection handle that throws.

## Queued expenses do not freeze exchange rates

When an expense is queued offline, only the amount and currency code are
stored. No exchange rate is frozen into the payload at queue time. The server
resolves the rate when the queue is flushed.

If the server cannot resolve a rate for a queued item at flush time, the item
stays in the queue and the error is surfaced to the user. This is preferable to
freezing a client-side rate that may be stale (the client's rate cache is at
most 30 minutes old) or unavailable (no rate was loaded yet offline).

## Service worker update strategy

`registerType: 'autoUpdate'` with `skipWaiting` and `clientsClaim` means a
newly deployed build takes effect on the next page reload without requiring the
user to manually dismiss an update prompt. For a single-operator personal
finance tool, silent auto-update is the right trade-off — there is no
multi-tab coordination concern and no risk of disrupting concurrent sessions.

## Rollback is image-level

There is no in-tree rollback path for the PWA. The `static/` vanilla-JS
predecessor and its FastAPI fallback were removed in the same commit that
completed the Vue migration. Rollback means redeploying the last container
image built before that change, which still ships the old app. This is
intentional — keeping a dead fallback path in-tree would accumulate drift and
create false confidence in a code path that is never tested.
