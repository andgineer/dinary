"""Load and validate the category vocabulary and onboarding templates.

``categories.yml`` (the ``.yml`` extension is deliberate) holds the full
category vocabulary: ``code -> {lang: name}``. Every other ``*.yaml`` file in
this package is a template: a complete mapping of every vocabulary code onto
a group, a visibility bucket (``visible``/``hidden``), and optional
per-template label overrides (``renames``).
"""

import dataclasses
from collections import Counter
from importlib import resources

import yaml

_PACKAGE = "dinary.category_templates"
_VOCABULARY_FILE = "categories.yml"


@dataclasses.dataclass(frozen=True, slots=True)
class Template:
    code: str
    names: dict[str, str]
    taglines: dict[str, str]
    groups: dict[str, dict[str, str]]
    renames: dict[str, dict[str, str]]
    visible: dict[str, list[str]]
    hidden: dict[str, list[str]]


def load_vocabulary() -> dict[str, dict[str, str]]:
    """Parse ``categories.yml`` into ``code -> {lang: name}``."""
    text = resources.files(_PACKAGE).joinpath(_VOCABULARY_FILE).read_text(encoding="utf-8")
    return yaml.safe_load(text)


def load_templates() -> list[Template]:
    """Parse every ``*.yaml`` template file, sorted alphabetically by filename."""
    package = resources.files(_PACKAGE)
    templates = []
    for entry in sorted(package.iterdir(), key=lambda p: p.name):
        if not entry.name.endswith(".yaml"):
            continue
        data = yaml.safe_load(entry.read_text(encoding="utf-8"))
        templates.append(
            Template(
                code=data["id"],
                names=data["names"],
                taglines=data["taglines"],
                groups=data["groups"],
                renames=data.get("renames", {}),
                visible=data["visible"],
                hidden=data["hidden"],
            ),
        )
    return templates


def validate(vocabulary: dict[str, dict[str, str]], templates: list[Template]) -> None:
    """Check vocabulary/template coverage and language-key consistency.

    Raises ``ValueError`` describing the first problem found.
    """
    if not templates:
        msg = "no templates to validate"
        raise ValueError(msg)

    vocab_codes = set(vocabulary)
    lang_keys = set(templates[0].names)
    _validate_language_keys(templates, lang_keys)
    _validate_vocabulary_translations(vocabulary, lang_keys)
    for template in templates:
        _validate_template_coverage(template, vocab_codes)


def _validate_language_keys(templates: list[Template], lang_keys: set[str]) -> None:
    """Check every template declares the same ``names``/``taglines`` language keys."""
    for template in templates:
        if set(template.names) != lang_keys:
            msg = (
                f"template {template.code!r}: 'names' language keys "
                f"{set(template.names)} != {lang_keys}"
            )
            raise ValueError(msg)
        if set(template.taglines) != lang_keys:
            msg = (
                f"template {template.code!r}: 'taglines' language keys "
                f"{set(template.taglines)} != {lang_keys}"
            )
            raise ValueError(msg)


def _validate_vocabulary_translations(
    vocabulary: dict[str, dict[str, str]],
    lang_keys: set[str],
) -> None:
    """Check every vocabulary entry has a translation for each template language."""
    for code, names in vocabulary.items():
        missing_langs = lang_keys - set(names)
        if missing_langs:
            msg = f"categories.yml entry {code!r} is missing translations for {missing_langs}"
            raise ValueError(msg)


def _validate_template_coverage(template: Template, vocab_codes: set[str]) -> None:
    """Check a template's ``visible``/``hidden`` placements and group references."""
    placed = [code for codes in template.visible.values() for code in codes]
    placed += [code for codes in template.hidden.values() for code in codes]

    duplicates = {code for code, count in Counter(placed).items() if count > 1}
    if duplicates:
        msg = f"template {template.code!r}: codes placed more than once: {duplicates}"
        raise ValueError(msg)

    placed_set = set(placed)
    missing_codes = vocab_codes - placed_set
    if missing_codes:
        msg = f"template {template.code!r}: missing vocabulary codes: {missing_codes}"
        raise ValueError(msg)
    unknown_codes = placed_set - vocab_codes
    if unknown_codes:
        msg = f"template {template.code!r}: references unknown codes: {unknown_codes}"
        raise ValueError(msg)

    referenced_groups = set(template.visible) | set(template.hidden)
    unknown_groups = referenced_groups - set(template.groups)
    if unknown_groups:
        msg = f"template {template.code!r}: references undeclared groups: {unknown_groups}"
        raise ValueError(msg)
