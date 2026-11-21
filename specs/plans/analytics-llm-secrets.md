# Plan: the chat's API key on a machine without the deploy config

Implementation is deferred until analytics has to run on a second machine. The direction is
chosen.

## The problem

`inv analytics` resolves the dashboard chat's API key from `.deploy/.env` on the machine it runs
on (`tasks/analytics.py` — the value comes from the env *file* via `dotenv_values`, not from the
process environment) and exports it into the marimo process.

That works while analytics runs on the machine that holds the deploy config. It breaks when
analytics runs somewhere that has only the analytics package installed — the operator has to
reproduce a file carrying a secret on every such machine.

## Constraints

- **Analytics never calls the running dinary server for the key** — a stated architectural
  decision (`specs/reference/analytics-ai.md`, "LLM strategy"). The only thing it fetches is the
  ledger snapshot.
- **The server's keys are not a source for the chat.** They are physically present in the
  replica file, because the snapshot is a full copy of the server database
  (`analytics-ledger-visibility.md`), but analytics must not read them: the broker owns that
  storage and its format, and the chat's model is paid while the pipeline's are free-tier, so
  the two do not share a key.
- **llmbroker changes go into llmbroker.** If the direction below needs anything llmbroker does
  not offer, that is discussed and released there first; dinary adds no workaround.

## Direction: the user enters the key once in the dashboard

When the chat has no key, the dashboard shows a key field in place of today's "add the key to
`.deploy/.env`" warning. The user pastes the key once; the chat works from then on, across
restarts. `.deploy/.env` stays supported and wins when it has the key, so the maintainer's
machine behaves as today.

### Where the key is kept

In its own file in the app-data directory, created with owner-only permissions — not in
`analytics.db`. `analytics.db` is backed up offsite (`backup-analytics-yadisk`), and a secret
must not ride along in that backup.

### How llmbroker gets it

`Broker(direct=[alias])` resolves the key from the process environment, then from a `.env`
beside the working directory. Two candidate mechanisms, to settle with llmbroker before coding:

1. The launcher (`tasks/analytics.py`) reads the stored key and exports it into the marimo
   process environment, exactly as it does today with the value from `.deploy/.env`. No
   llmbroker change. A key entered in the running dashboard takes effect by setting the
   variable in the running process.
2. llmbroker accepts an explicit key source. Cleaner, but an llmbroker feature.

Recommended: mechanism 1, after confirming with the llmbroker side that feeding the documented
environment source from a host-owned store is the intended use and not a workaround.

## Changes (when scheduled)

| File | Change |
|---|---|
| `src/dinary_analytics/paths.py` | path of the key file |
| `src/dinary_analytics/llm.py` | `chat_key_available()` also counts the stored key; store/read helpers; the no-key and rejected-key replies point at the dashboard field |
| `src/dinary_analytics/notebooks/dashboard.py` | key field shown when no key resolves; a "replace key" control when the provider rejects it |
| `tasks/analytics.py` | key from `.deploy/.env` first, the stored key second |
| `docs/src/en/analytics.md`, `docs/src/ru/analytics.md` | setup section |

## Tests

- Stored key is found by `chat_key_available()`; a blank stored value counts as absent.
- The key file is created with owner-only permissions.
- `.deploy/.env` wins over the stored key in the launcher.
- The analytics backup does not contain the key file.
- Dashboard cell: field shown without a key, hidden with one.

## When done

Rewrite the key sentence of "LLM strategy" in `specs/reference/analytics-ai.md`.
