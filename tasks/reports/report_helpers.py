"""Pure helpers for parsing CLI flags in report tasks.

No SSH, no invoke Context, no .deploy/.env dependency.
"""


def extract_format_flags(flags: list[str]) -> tuple[bool, bool, list[str]]:
    """``--csv``/``--json`` select the local output format; other filters travel
    through to the remote module. The remote always runs in JSON mode."""
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
    """Cosmetic only — drives the rich table title; row filtering already
    happened on the remote query, so a malformed value just degrades header text."""
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
