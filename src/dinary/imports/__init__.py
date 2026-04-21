"""Historical bootstrap import logic.

Modules in this package handle the one-time (``inv import-budget``)
import of year-by-year Google Sheet data into DuckDB. They are
year-aware and use ``import_mapping`` for 2D→3D resolution.

Runtime sheet logging (the append-only path triggered by
``POST /api/expenses``) lives in ``dinary.services.sheet_logging`` and uses the
separate ``sheet_mapping`` table, populated exclusively from the
hand-curated ``map`` worksheet tab (see ``sheet_mapping.py``). The two
mapping tables are independent: changes to one do not automatically
propagate to the other.
"""
