"""Contract test: every URL used in webapp/src/api/*.js must exist in the backend.

Extracts URL patterns from the webapp JS source, normalises JS template
literals to OpenAPI path syntax (``${id}`` → ``{id}``), then checks each
against the FastAPI OpenAPI schema.  Fails when a frontend file references
an endpoint that the backend no longer exposes.
"""

import re
from pathlib import Path

import allure
import pytest

from dinary.main import create_app

_WEBAPP_API_DIR = Path(__file__).resolve().parents[1] / "webapp" / "src" / "api"

# JS template literal segment → generic OpenAPI placeholder.
# Order matters: longer patterns first.
_PLACEHOLDER_RE = re.compile(
    r"\$\{encodeURIComponent\([^}]+\)\}"  # ${encodeURIComponent(x)} → {x}
    r"|\$\{[^}]+\}",  # ${anything}              → {param}
)

# Query-string suffix (e.g. "?page=${page}&page_size=${pageSize}")
_QUERY_RE = re.compile(r"\?.*$")


def _normalise(js_url: str) -> str:
    """Convert a JS URL string to an OpenAPI-style path for comparison."""
    path = _QUERY_RE.sub("", js_url)
    path = _PLACEHOLDER_RE.sub("{param}", path)
    return path.rstrip("/")


def _extract_webapp_urls() -> set[str]:
    """Collect all /api/... URL literals from webapp/src/api/*.js."""
    urls: set[str] = set()
    for js_file in _WEBAPP_API_DIR.glob("*.js"):
        text = js_file.read_text(encoding="utf-8")
        # String literals: "/api/..."
        urls.update(re.findall(r'"/api/[^"]*"', text))
        # Template literals: `/api/...`
        urls.update(re.findall(r"`/api/[^`]*`", text))
    # Strip surrounding quotes/backticks
    return {u.strip("'\"`") for u in urls}


@allure.epic("Expenses")
@allure.feature("API")
class TestWebappApiContract:
    @pytest.fixture(scope="class")
    def backend_paths(self):
        app = create_app()
        schema = app.openapi()
        # Normalise both sides: replace {any_name} with {param}
        _param = re.compile(r"\{[^}]+\}")
        return {_param.sub("{param}", p.rstrip("/")) for p in schema["paths"]}

    def test_all_webapp_urls_exist_in_backend(self, backend_paths):
        """Every URL the webapp calls must exist in the backend OpenAPI schema."""
        webapp_urls = _extract_webapp_urls()

        missing: list[tuple[str, str]] = []
        for raw in sorted(webapp_urls):
            normalised = _normalise(raw)
            if normalised not in backend_paths:
                missing.append((raw, normalised))

        if missing:
            lines = "\n".join(f"  {raw!r}  →  {norm!r}" for raw, norm in missing)
            pytest.fail(
                f"{len(missing)} webapp URL(s) not found in backend OpenAPI schema:\n{lines}\n\n"
                f"Backend paths:\n" + "\n".join(f"  {p}" for p in sorted(backend_paths)),
            )
