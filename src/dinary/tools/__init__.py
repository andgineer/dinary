"""Operator-facing CLI utilities that aren't reports or imports.

This package holds ad-hoc diagnostic / inspection tools invoked
exclusively via ``inv`` tasks. They are intentionally kept separate
from ``dinary.reports`` (which have a stable aggregation contract)
and ``dinary.imports`` (which mutate the ledger) — the tools here
are read-only, free-form, and evolve independently.
"""
