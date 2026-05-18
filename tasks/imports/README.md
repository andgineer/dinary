Historical bootstrap import logic.

Modules here handle the one-time (`inv import-budget`) import of year-by-year
Google Sheet data into the SQLite ledger. They are year-aware and use
`import_mapping` for 2D→3D resolution.
