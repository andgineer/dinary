# Problem: LLM key resolution for analytics on a separate machine

No solution chosen yet. This file states the problem only.

## The problem

`inv analytics` resolves the dashboard chat's API key from `.deploy/.env` on the machine it runs
on (`tasks/analytics.py` — the value comes from the env *file* via `dotenv_values`, not from the
process environment) and exports it into the marimo process.

That works while analytics runs on the machine that holds the deploy config. It breaks when
analytics runs somewhere that has only the analytics package installed — the operator has to
reproduce a file carrying a secret on every such machine.

## Constraints any solution has to respect

- **Analytics never calls the running dinary server** — a stated architectural decision
  (`specs/reference/analytics-ai.md`, "LLM strategy"). Fetching the key from a dinary endpoint is
  not a free option; it would need that decision revisited first.
- **The server's own keys are not reachable from analytics.** The broker owns its key storage
  inside its own `llmbroker_`-prefixed schema; there is no dinary table to read keys from, and no
  established way to export values out of llmbroker. The chat's model is paid and the pipeline's
  are free-tier, so the two do not share a key in any case.
- **Anything that moves key values over the network needs an auth layer** the app does not have
  today.

## Status

Deferred until analytics actually has to run on a separate machine. Until then the single-file
requirement is acceptable.
