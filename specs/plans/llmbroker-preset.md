# Switch provider seeding to llmbroker preset command

## Context

dinary's initial provider seeding currently requires the operator to manually create
`.deploy/llms.toml`. The llmbroker repository already ships curated preset TOML files
at `presets/freetier.toml` and `presets/smart-freetier.toml`, but has no CLI command
to print them to stdout.

Once a `preset` subcommand is added to the llmbroker CLI, the operator can replace
manual file authoring with:

```bash
python -m llmbroker preset freetier > .deploy/llms.toml
```

No separate sync step is needed — `main.py` already seeds the SQLite registry
programmatically from `.deploy/llms.toml` on first boot via
`AsyncBroker(seed=Registry(...), seed_policy=SeedPolicy.ADD)`.

To scaffold the required API-key env vars after generating the file:

```bash
python -m llmbroker env .deploy/llms.toml >> .deploy/.env
```

(`env` already exists in the CLI.)

## When to apply

Once `python -m llmbroker preset <name>` is released in llmbroker.

## Changes

- Remove the manual file-authoring step from deployment instructions.
- Replace with the two commands above.
- Update `.deploy.example/llms.toml` header comment to reference the preset command.

## What does not change

- The seeding mechanism in `main.py` — `seed` + `SeedPolicy.ADD` stays as-is.
- Admin edits survive reboots because `SeedPolicy.ADD` is a no-op for names already
  in the SQLite registry.
- `specs/reference/llmbroker-integration.md` describes the stable wiring; only
  the provisioning workflow changes.
