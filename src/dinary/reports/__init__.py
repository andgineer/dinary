"""Read-only ledger aggregation views.

Unlike :mod:`dinary.imports.report_2d_3d`, which reviews the quality of
the 2D-to-3D migration *process*, modules in this subpackage are plain
ledger views: they query whatever rows are currently in the SQLite
ledger and render them for human inspection. They take no part in the
write path and share no state with the import flow.

Each module is invokable as ``python -m dinary.reports.<name>`` with a
common ``--csv`` escape hatch; the corresponding ``inv show-*`` tasks
in ``tasks.py`` are thin wrappers that add ``--remote`` dispatch.
"""
