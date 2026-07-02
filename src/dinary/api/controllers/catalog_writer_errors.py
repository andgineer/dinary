"""Shared exception types and result dataclasses for catalog write operations."""

from dataclasses import dataclass
from typing import Literal

CatalogKind = Literal["category_group", "category", "event", "tag"]

AddStatus = Literal["created", "reactivated", "noop"]

#: See specs/reference/catalog-api.md "Soft vs hard delete".
DeleteStatus = Literal["hard", "soft"]


@dataclass(frozen=True, slots=True)
class DeleteResult:
    """``usage_count`` is zero for the hard-delete branch, >0 for soft-delete."""

    status: DeleteStatus
    usage_count: int


@dataclass(frozen=True, slots=True)
class AddResult:
    """``status`` distinguishes a new INSERT, a reactivate-in-place, and a silent
    no-op (active row with matching fields)."""

    id: int
    status: AddStatus


class CatalogWriteError(Exception):
    """Base class for catalog writer errors raised to API callers."""

    http_status: int = 422

    def __init__(self, message: str, *, http_status: int | None = None) -> None:
        super().__init__(message)
        if http_status is not None:
            self.http_status = http_status


class CatalogInUseError(CatalogWriteError):
    """Only raised for ``category_group`` (children must be relocated/removed
    first). Categories/events/tags instead auto-degrade to soft-delete, since
    SQLite only enforces FK constraints on DELETE and key-column UPDATE, not on
    other column updates."""

    http_status = 409

    def __init__(self, kind: CatalogKind, row_id: int, usage_count: int) -> None:
        if kind == "category_group":
            detail = f"still has {usage_count} child categor{'y' if usage_count == 1 else 'ies'}"
            hint = "relocate or delete the categories first"
        else:
            detail = f"still referenced by {usage_count} expense row(s)"
            hint = "retire the referencing expenses first"
        super().__init__(f"{kind} id={row_id} is {detail}; {hint}")
        self.kind = kind
        self.row_id = row_id
        self.usage_count = usage_count


class CatalogNotFoundError(CatalogWriteError):
    http_status = 404


class CatalogConflictError(CatalogWriteError):
    http_status = 409
