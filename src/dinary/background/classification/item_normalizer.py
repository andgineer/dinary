import re

# Lidl/DM format: "Rotkvica, veza/KOM/0082275" — strip /UNIT/BARCODE suffix.
_LIDL_BARCODE = re.compile(r"\s*/(?:kom|kg|g|ml|l|cl|dl|pc|pcs)/\S+.*$", re.IGNORECASE)

# VAT category code appended by some stores, e.g. "(E)", " (Đ)".
_VAT_CODE = re.compile(r"\s+\([A-ZĐŽĆŠA-ZА-Я]\)\s*$", re.IGNORECASE)

# Trailing unit specs: "550G", "1L", "8KOM", "750ML 6KOM", "0,5KG".
_TRAILING_UNITS = re.compile(
    r"(?:\s+\d+[\d.,]*\s*(?:g|kg|ml|l|cl|dl|kom|pc|pcs|kos))+\s*$",
    re.IGNORECASE,
)

# Leading unit prefix: METRO format "1000ML NAME", "0.33L NAME".
_LEADING_UNIT = re.compile(r"^\d+[\d.,]*\s*(?:g|kg|ml|l|cl|dl)\s+", re.IGNORECASE)

_EXTRA_SPACES = re.compile(r"\s+")


def normalize_item_name(raw: str) -> str:
    """Lowercase and strip store-specific suffixes and unit tokens from a raw receipt item name."""
    name = raw.lower()
    name = _LIDL_BARCODE.sub("", name)
    name = _VAT_CODE.sub("", name)
    name = _TRAILING_UNITS.sub("", name)
    name = _LEADING_UNIT.sub("", name)
    return _EXTRA_SPACES.sub(" ", name).strip()
