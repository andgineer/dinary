"""Pure helpers for parsing CLI flags in report tasks.

No SSH, no invoke Context, no .deploy/.env dependency.
"""


def extract_format_flags(flags: list[str]) -> tuple[bool, bool, list[str]]:
    """Split ``flags`` into ``(as_csv, as_json, remaining)``.

    ``--csv`` / ``--json`` select the local output format and are
    consumed here. Filters (``--year``, ``--month``, ...) stay in
    ``remaining`` and travel through to the remote report module
    (they affect which rows come back). The remote always runs in
    JSON mode; ``--csv`` / ``--json`` never reach it.
    """
    as_csv = False
    as_json = False
    remaining: list[str] = []
    for flag in flags:
        if flag == "--csv":
            as_csv = True
        elif flag == "--json":
            as_json = True
        else:
            remaining.append(flag)
    return as_csv, as_json, remaining


def extract_year_month(filter_flags: list[str]) -> tuple[int | None, tuple[int, int] | None]:
    """Pull ``--year YYYY`` / ``--month YYYY-MM`` out of filter flags.

    The values are cosmetic — they drive the expenses rich table
    title only. Row filtering has already happened on the remote
    query, so a missing / malformed value here just degrades the
    header text.
    """
    year: int | None = None
    month: tuple[int, int] | None = None
    it = iter(filter_flags)
    for token in it:
        if token == "--year":
            try:
                year = int(next(it))
            except (StopIteration, ValueError):
                year = None
        elif token == "--month":
            value = next(it, "")
            parts = value.split("-")
            if len(parts) == 2:
                try:
                    month = (int(parts[0]), int(parts[1]))
                except ValueError:
                    month = None
    return year, month
