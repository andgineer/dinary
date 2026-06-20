# Switch provider seeding to llmbroker preset command

## Context

dinary's initial provider seeding currently requires the operator to manually create
`.deploy/llm_providers.toml`. The `preset` CLI command (Phase 2 of llmbroker) will
automate fetching a curated provider list from the llmbroker repository.

## When to apply

Once `python -m llmbroker preset freetier` is released (llmbroker Phase 2).

## Changes

Remove the manual step from the deployment instructions and replace with:

```bash
python -m llmbroker preset freetier > .deploy/llm_providers.toml
python -m llmbroker sync .deploy/llm_providers.toml \
    --into sqlite:.deploy/dinary.db --policy if_empty
```

The `sync` step can also be wired into the deploy task so first-boot seeding is
automatic. Admin edits survive because `--policy if_empty` is a no-op on a
non-empty registry.

To update the provider list without clobbering admin edits:

```bash
python -m llmbroker preset freetier > .deploy/llm_providers.toml
python -m llmbroker sync .deploy/llm_providers.toml \
    --into sqlite:.deploy/dinary.db --policy add
```

## What does not change

The lifespan does not auto-seed — seeding remains an explicit operator step.
The `specs/reference/llmbroker-integration.md` spec describes the stable wiring;
only the provisioning workflow changes.
