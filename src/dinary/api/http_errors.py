"""Shared helpers for translating domain exceptions into HTTP errors."""

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException


@contextmanager
def value_error_as_422() -> Iterator[None]:
    """Translate a ``ValueError`` raised inside the block into ``HTTPException(422)``."""
    try:
        yield
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
