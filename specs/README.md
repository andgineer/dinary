# Specs

## Scope

Specs capture architectural decisions and business requirements.

Examples of correct spec content:
- "Every expense created from a receipt must have a matching rule."
- "Retry receipt processing every 15 minutes on day 1, then once a day indefinitely."

Never include implementation details (function signatures, argument names, field names, internal class structure). The code is the source of truth for those.

## Current state only

Specs describe the system as it is now. Do not record refactoring history, past alternatives, or the sequence of changes that led to the current design — git history is the record for those. If the current rule differs from a past one, state the current rule only.
