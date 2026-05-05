"""Local development tasks: version, test, dev, build-static, backup."""

import re
import shutil
import subprocess
import sys
from pathlib import Path

from invoke import task

_WEBAPP_DIR = Path("webapp")
_STATIC_DIR = Path("_static")


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
    js_result = c.run("npm --prefix webapp test", warn=True)
    failed = []
    if py_result is not None and py_result.exited != 0:
        failed.append(f"pytest (exit {py_result.exited})")
    if js_result is not None and js_result.exited != 0:
        failed.append(f"npm --prefix webapp test (exit {js_result.exited})")
    if failed:
        print(f"Test failures: {', '.join(failed)}")
        sys.exit(1)


@task
def pre(c):
    """Run pre-commit checks."""
    c.run("pre-commit run --verbose --all-files")


def _tailscale_serve_start(port: int) -> str | None:
    if not shutil.which("tailscale"):
        return None
    result = subprocess.run(
        ["tailscale", "serve", "--bg", str(port)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"=== tailscale serve failed: {(result.stderr or result.stdout).strip()} ===")
        return None
    match = re.search(r"https://\S+", result.stdout + result.stderr)
    return match.group(0).rstrip("/") if match else None


def _tailscale_serve_stop() -> None:
    if shutil.which("tailscale"):
        subprocess.run(["tailscale", "serve", "off"], check=False)


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
        "rebuild": (
            "Force a full PWA rebuild even when ``_static/index.html`` "
            "already exists. Use after editing anything under ``webapp/``."
        ),
    },
)
def dev(c, port=8000, sheet_logging=False, reset=False, rebuild=False):
    """Run the FastAPI server locally with auto-reload for PWA debugging.

    - Listens on http://127.0.0.1:<port> — open that URL in your
      browser instead of pushing to Oracle Cloud on every iteration.
    - ``uvicorn --reload`` watches ``src/`` and re-imports on Python
      changes.
    - The PWA source lives in ``webapp/`` (Vue 3 + Pinia + Vite +
      ``vite-plugin-pwa``); FastAPI serves the **built** assets from
      ``_static/``. **Run ``inv build-static`` explicitly after any
      change under ``webapp/``** — uvicorn does not rebuild the PWA
      on file changes. As a safety net, this task auto-runs
      ``inv build-static`` if ``_static/index.html`` is missing, but
      it will NOT auto-rebuild a stale ``_static/`` (one whose
      ``index.html`` already exists from a previous build). For HMR
      while iterating on Vue, run ``npm --prefix webapp run dev`` in
      a separate terminal — it serves on :5173 and proxies ``/api``
      to this server.
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
    if rebuild:
        _run_build(c, dev_mode=True)
    else:
        _ensure_static_built(c)

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
    url = _tailscale_serve_start(port)
    if url:
        print(f"\n=== Tailscale HTTPS: {url} ===\n")
    else:
        print(f"\n=== Local only: http://127.0.0.1:{port} ===\n")
    try:
        c.run(cmd, pty=True)
    finally:
        _tailscale_serve_stop()


def _build_version() -> str:
    """Return the git tag on HEAD (v-prefix stripped), or the short commit hash."""
    try:
        tag = subprocess.check_output(
            ["git", "describe", "--exact-match", "--tags", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return tag.lstrip("v")
    except subprocess.CalledProcessError:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True,
        ).strip()


def _run_build(c, dev_mode: bool = False) -> None:
    if not _WEBAPP_DIR.is_dir():
        msg = (
            f"Cannot build PWA: {_WEBAPP_DIR}/ is missing. The Vue 3 "
            "source must be present at the repo root."
        )
        raise RuntimeError(msg)

    version = _build_version()
    data = Path("data")

    if not (_WEBAPP_DIR / "node_modules").is_dir():
        print("=== npm ci (webapp/) ===")
        c.run("npm --prefix webapp ci --no-audit --no-fund")

    env_prefix = "VITE_DEV_MODE=true " if dev_mode else ""
    print(f"=== Building _static/ via Vite (version {version}) ===")
    c.run(f"{env_prefix}npm --prefix webapp run build")

    index = _STATIC_DIR / "index.html"
    if not index.is_file():
        msg = (
            f"Vite build did not produce {index}. Check the output of "
            "``npm --prefix webapp run build`` for errors."
        )
        raise RuntimeError(msg)

    data.mkdir(exist_ok=True)
    (data / ".deployed_version").write_text(version)
    (_STATIC_DIR / "version.json").write_text(f'{{"version": "{version}"}}\n')
    print(f"Built _static/ with version {version}")


def _ensure_static_built(c) -> None:
    if not (_STATIC_DIR / "index.html").is_file():
        build_static(c)


@task(name="build-static")
def build_static(c):
    """Build the Vue 3 PWA into ``_static/`` and write the deployed version.

    The Vue source is in ``webapp/``. The Vite config writes to
    ``_static/`` at the repo root; ``vite-plugin-pwa`` regenerates the
    service worker on every build, and the build version (git tag /
    short hash) is injected via ``__APP_VERSION__`` at build time.

    This task additionally writes ``data/.deployed_version`` so that
    ``GET /api/version`` keeps reporting the deployed git ref the way
    the older copy-only pipeline did.

    Run it after editing anything under ``webapp/``.
    ``npm ci`` runs automatically only when ``webapp/node_modules`` is absent.
    """
    _run_build(c)
