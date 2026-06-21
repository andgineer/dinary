# Analytics LLM secrets — future resolution

## Current state

`inv analytics` resolves the Gemini API key by reading `.deploy/llms.toml` (for the
`api_key_ref` env-var name) and then looking up that env var. This requires `.deploy/`
to exist on the machine running analytics and the env vars to be set there. If either
is absent the task exits with a clear error.

This works when analytics runs on the same machine as the deploy config. It breaks
when analytics runs on a machine that only has analytics installed and network access
to the dinary API.

## Problem

The dinary server stores resolved keys in `llmbroker_secrets` (sqlite). The analytics
process has no access to that DB. Requiring `.deploy/llms.toml` + env vars on every
analytics machine is operationally heavy.

## Options to decide later

**Option A — dinary API endpoint**

Add `GET /api/llm/keys` (admin-only) that returns `{ref: resolved_value}` for all
secrets in `llmbroker_secrets`. Analytics fetches this endpoint and resolves keys
without needing local files.

Upside: zero local config on the analytics machine.
Downside: exposes key values over the API; requires auth (currently no auth layer).

**Option B — local secrets file**

Define a minimal `~/.config/dinary/secrets.toml` (or `.deploy/secrets.toml`) that
analytics reads directly. Operator copies it once; it has only the key values needed
by analytics (e.g. `GEMINI_API_KEY`). Not synced during `inv deploy`.

Upside: no API changes; keys never leave the machine.
Downside: another file for the operator to maintain.

## Decision

Deferred. Current `.deploy/llms.toml` + env vars approach is acceptable until
analytics needs to run on a separate machine in practice.
