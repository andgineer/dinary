"""Shared exception types and result dataclasses for catalog write operations."""

from dataclasses import dataclass
from typing import Literal

CatalogKind = Literal["category_group", "category", "event", "tag"]

AddStatus = Literal["created", "reactivated", "noop"]

#: ``hard`` = row physically removed; ``soft`` = row flipped to
#: ``is_active=FALSE`` because it's still referenced by the ledger and
#: removing it would orphan historical rows. The admin API surfaces
#: the distinction so the PWA can tell the operator "still available
#: under Show inactive" vs "gone for good".
DeleteStatus = Literal["hard", "soft"]


@dataclass(frozen=True, slots=True)
class DeleteResult:
    """Return value of ``delete_*`` helpers.

    ``status`` reports whether the row was physically removed or
    soft-retired (``is_active=FALSE``). ``usage_count`` is the number
    of referencing ledger rows observed at decision time — zero for
    the hard-delete branch, >0 for the soft-delete branch.
    """

    status: DeleteStatus
    usage_count: int


@dataclass(frozen=True, slots=True)
class AddResult:
    """Return value of ``add_group`` / ``add_category`` / ``add_event`` / ``add_tag``.

    ``id`` is the row id (existing or new). ``status`` distinguishes
    between a brand-new INSERT, a reactivate-in-place, and a fully
    silent no-op (active row with matching fields). Admin-API callers
    propagate ``status`` to the PWA response so the UI can tell the
    user "reactivated existing" vs "created new".
    """

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
    """Delete or deactivate blocked because the row is still referenced.

    Only raised for ``category_group``: ``usage_count`` counts child
    categories (active or inactive). A group with any children cannot
    be deleted or deactivated until the categories are relocated or
    removed.

    Categories / events / tags do *not* raise this — ``delete_*`` on a
    referenced row auto-degrades to soft-delete (see
    ``DeleteResult.status``) and ``edit_*`` on a referenced row
    accepts any column mix including ``is_active=FALSE`` combined with
    rename / ``group_id`` move. The former is a policy choice ("retire
    but keep pointable"); the latter is safe because SQLite enforces
    FK constraints on ``DELETE`` (and on ``UPDATE`` of referenced key
    columns) only, not on ``UPDATE`` of non-key columns.
    """

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
