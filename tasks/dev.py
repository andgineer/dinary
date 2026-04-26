"""Local development tasks: version, test, dev, build-static, backup."""

import shutil
import subprocess
import sys
from pathlib import Path

from invoke import task

from dinary.__about__ import __version__


@task
def version(_c):
    """Show the current version."""
    with open("src/dinary/__about__.py") as f:
        version_line = f.readline()
        version_num = version_line.split('"')[1]
        print(version_num)
        return version_num


def ver_task_factory(version_type: str):
    @task
    def ver(c):
        """Bump the version."""
        c.run(f"./scripts/verup.sh {version_type}")

    ver.__doc__ = f"Bump the {version_type} version component."
    return ver


@task
def reqs(c):
    """Upgrade requirements including pre-commit."""
    c.run("pre-commit autoupdate")
    c.run("uv lock --upgrade")


def docs_task_factory(language: str):
    @task
    def docs(c):
        """Docs preview for the language specified."""
        c.run("open -a 'Google Chrome' http://127.0.0.1:8000/dinary/")
        c.run(f"scripts/build-docs.sh --copy-assets {language}")
        c.run("mkdocs serve -f docs/_mkdocs.yml")

    docs.__doc__ = f"Preview {language} docs locally (builds and serves with mkdocs)."
    return docs


@task
def uv(c):
    """Install or upgrade uv."""
    c.run("curl -LsSf https://astral.sh/uv/install.sh | sh")


@task
def test(c):
    """Run all tests (Python + JavaScript) with Allure results.

    Both suites always run (so npm test is not skipped on a pytest
    failure), but the task exits non-zero if either failed so CI can
    distinguish "all green" from "partial green".
    """
    c.run("rm -rf allure-results")
    py_result = c.run("uv run pytest tests/ -v --alluredir=allure-results", warn=True)
    js_result = c.run("npm test", warn=True)
    failed = []
    if py_result is not None and py_result.exited != 0:
        failed.append(f"pytest (exit {py_result.exited})")
    if js_result is not None and js_result.exited != 0:
        failed.append(f"npm test (exit {js_result.exited})")
    if failed:
        print(f"Test failures: {', '.join(failed)}")
        sys.exit(1)


@task
def pre(c):
    """Run pre-commit checks."""
    c.run("pre-commit run --verbose --all-files")


@task(
    help={
        "port": "TCP port to listen on (default 8000).",
        "sheet-logging": (
            "Opt back into Google-Sheets logging. OFF by default so debug "
            "expenses don't leak to the prod logging spreadsheet."
        ),
        "reset": (
            "Wipe ``data/dinary.db`` (plus its WAL / SHM sidecars) before "
            "starting, then re-seed the catalog from the hardcoded "
            "taxonomy (groups, categories, events, tags) so PWA dropdowns "
            "have rows. Best-effort kills any lingering local uvicorn "
            "process holding the DB. Non-destructive if no DB exists yet."
        ),
    },
)
def dev(c, port=8000, sheet_logging=False, reset=False):
    """Run the FastAPI server locally with auto-reload for PWA debugging.

    - Listens on http://127.0.0.1:<port> — open that URL in your
      browser instead of pushing to Oracle Cloud on every iteration.
    - ``uvicorn --reload`` watches ``src/`` and re-imports on Python
      changes. Static files (``static/``) are served straight from
      disk on every request, so editing ``static/css/style.css`` or
      ``static/js/app.js`` only needs a browser refresh — no server
      restart, no rebuild.
    - Sheet-logging is **disabled** by default
      (``DINARY_SHEET_LOGGING_SPREADSHEET=`` overrides the value
      from ``.deploy/.env``), so test expenses you create in dev
      do NOT show up in the prod Google Sheet. Pass
      ``--sheet-logging`` to opt in (rare; for debugging the drain
      loop itself).
    - Uses ``data/dinary.db`` as the local DB (SQLite in WAL mode).
      Schema migrations run automatically on server startup via the
      FastAPI lifespan (``_lifespan -> ledger_repo.init_db``), so
      editing a migration and restarting ``inv dev`` is enough — no
      separate ``migrate`` step. For a clean slate use ``--reset``.
    - To work against real prod data: run ``inv backup`` first to
      fetch a consistent snapshot into ``~/Library/dinary/<ts>/``,
      then copy the resulting ``dinary.db`` into ``data/``.

    PWA caching tip: once the service worker is registered the
    browser will serve cached assets even after you edit them. In
    Chrome DevTools open ``Application -> Service Workers`` and
    tick ``Update on reload`` (or ``Bypass for network``) for fast
    iteration. Hard-refresh (Cmd-Shift-R) also bypasses the SW
    for that one navigation.
    """
    if reset:
        subprocess.run(
            ["pkill", "-f", "uvicorn dinary"],
            check=False,
        )
        for fn in (
            "data/dinary.db",
            "data/dinary.db-wal",
            "data/dinary.db-shm",
        ):
            p = Path(fn)
            if p.exists():
                p.unlink()
                print(f"Removed {p}")
        c.run(
            "uv run python -c 'from dinary.services.seed_config "
            "import bootstrap_catalog; import json; "
            "print(json.dumps(bootstrap_catalog()))'",
        )

    overrides = []
    if not sheet_logging:
        overrides += [
            "DINARY_SHEET_LOGGING_SPREADSHEET=",
            "DINARY_SHEET_LOGGING_DRAIN_INTERVAL_SEC=0",
        ]
    prefix = (" ".join(overrides) + " ") if overrides else ""
    cmd = (
        f"{prefix}uv run uvicorn dinary.main:app "
        f"--reload --reload-dir src "
        f"--host 127.0.0.1 --port {port}"
    )
    c.run(cmd, pty=True)


@task(name="build-static")
def build_static(c):  # noqa: ARG001
    """Replace __VERSION__ in static/ files, write to _static/."""
    src = Path("static")
    dst = Path("_static")
    data = Path("data")

    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)

    for filepath in [dst / "js" / "app.js", dst / "sw.js"]:
        text = filepath.read_text()
        filepath.write_text(text.replace("__VERSION__", __version__))

    data.mkdir(exist_ok=True)
    (data / ".deployed_version").write_text(__version__)
    print(f"Built _static/ with version {__version__}")
