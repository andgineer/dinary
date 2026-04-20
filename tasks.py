import base64
import os
import re as _re
import shlex
import shutil
import sys
from datetime import datetime as _dt
from pathlib import Path

from dinary.__about__ import __version__
from dotenv import dotenv_values
from invoke import Collection, Context, task


def get_allowed_doc_languages():
    build_docs_file_name = "scripts/build-docs.sh"
    try:
        with open(build_docs_file_name) as f:
            for line in f:
                if "LANGUAGES=" in line:
                    value = line.split("=", 1)[1].strip()
                    value = value.strip('"').strip("'")
                    langs = value.split()
                    return [lang.strip() for lang in langs]
    except FileNotFoundError:
        print(f"No {build_docs_file_name} file found")
    return ["en", "bg", "de", "es", "fr", "ru"]


ALLOWED_DOC_LANGUAGES = get_allowed_doc_languages()
ALLOWED_VERSION_TYPES = ["release", "bug", "feature"]

REPO_URL = "https://github.com/andgineer/dinary-server.git"

DINARY_SERVICE = """\
[Unit]
Description=dinary-server
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/dinary-server
EnvironmentFile=/home/ubuntu/dinary-server/.env
ExecStart=/home/ubuntu/.local/bin/uv run uvicorn dinary.main:app --host {host} --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""

CLOUDFLARED_SERVICE = """\
[Unit]
Description=Cloudflare Tunnel for dinary
After=network.target

[Service]
Type=simple
User=ubuntu
ExecStart=/usr/bin/cloudflared tunnel run
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""

VALID_TUNNELS = ("tailscale", "cloudflare", "none")


def _env():
    return dotenv_values(".env")


def _host():
    host = _env().get("DINARY_DEPLOY_HOST")
    if not host:
        print("Set DINARY_DEPLOY_HOST in .env  (e.g. ubuntu@1.2.3.4)")
        sys.exit(1)
    return host


def _tunnel():
    tunnel = (_env().get("DINARY_TUNNEL") or "tailscale").lower()
    if tunnel not in VALID_TUNNELS:
        print(f"DINARY_TUNNEL must be one of: {', '.join(VALID_TUNNELS)}")
        sys.exit(1)
    return tunnel


def _ssh(c, cmd):
    b64 = base64.b64encode(cmd.encode()).decode()
    c.run(f"ssh {_host()} 'echo {b64} | base64 -d | bash'")


def _ssh_sudo(c, cmd):
    _ssh(c, f"sudo {cmd}")


def _write_remote_file(c, path, content):
    b64 = base64.b64encode(content.encode()).decode()
    c.run(f"ssh {_host()} 'echo {b64} | base64 -d | sudo tee {path} > /dev/null'")


def _create_service(c, name, content):
    _write_remote_file(c, f"/etc/systemd/system/{name}.service", content)
    _ssh_sudo(c, "systemctl daemon-reload")
    _ssh_sudo(c, f"systemctl enable {name}")
    _ssh_sudo(c, f"systemctl restart {name}")


_ENV_SAFE_RE = _re.compile(r"^[A-Za-z0-9_./:@\-]*$")


def _systemd_quote(value: str | None) -> str:
    r"""Quote a value for systemd's ``EnvironmentFile=`` parser.

    systemd uses shell-like double-quote semantics: bare values may not
    contain spaces, quotes, or special chars; quoted values back-slash
    escape ``\``, ``"``, and ``$`` as in POSIX shell. We wrap any value
    containing unsafe characters in double quotes so JSON, base64 blobs
    with ``+``, and URLs with ``=``/``?`` round-trip correctly even
    though ``dotenv_values()`` strips the surrounding quotes locally.
    """
    if not value:
        return ""
    if _ENV_SAFE_RE.match(value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
    return f'"{escaped}"'


def _sync_remote_env(c):
    """Sync all DINARY_* settings from local .env to the server (idempotent).

    Skips deploy-only keys (DINARY_DEPLOY_HOST, DINARY_TUNNEL) that are
    only meaningful on the operator's machine. Values are quoted using
    systemd's ``EnvironmentFile=`` syntax so JSON blobs (e.g.
    ``DINARY_IMPORT_SOURCES_JSON``) and base64-encoded credentials
    survive the transfer intact. The remote file is also chowned to
    ``ubuntu`` so the systemd unit (``User=ubuntu``) can read it.
    """
    env = _env()
    skip = {"DINARY_DEPLOY_HOST", "DINARY_TUNNEL"}
    lines = [
        f"{k}={_systemd_quote(v)}\n"
        for k, v in env.items()
        if k.startswith("DINARY_") and k not in skip
    ]
    if not lines:
        print("No DINARY_* settings found in local .env")
        sys.exit(1)
    remote_path = "/home/ubuntu/dinary-server/.env"
    _write_remote_file(c, remote_path, "".join(lines))
    _ssh_sudo(c, f"chown ubuntu:ubuntu {remote_path} && chmod 600 {remote_path}")


def _setup_tailscale(c):
    print("=== Installing Tailscale ===")
    _ssh(c, "command -v tailscale || curl -fsSL https://tailscale.com/install.sh | sh")
    _ssh_sudo(c, "tailscale up")

    print("=== Enabling Tailscale Serve (tailnet only) ===")
    _ssh_sudo(
        c,
        "tailscale serve reset 2>/dev/null; tailscale funnel reset 2>/dev/null; tailscale serve --bg 8000",
    )


def _setup_cloudflare(c):
    print("=== Installing cloudflared ===")
    _ssh(
        c,
        "command -v cloudflared || "
        "(curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 "
        "-o /tmp/cloudflared && sudo install /tmp/cloudflared /usr/bin/cloudflared)",
    )
    _ssh(c, "cloudflared tunnel login")

    print("=== Creating cloudflared service ===")
    _create_service(c, "cloudflared", CLOUDFLARED_SERVICE)


@task
def version(_c: Context):
    """Show the current version."""
    with open("src/dinary/__about__.py") as f:
        version_line = f.readline()
        version_num = version_line.split('"')[1]
        print(version_num)
        return version_num


def ver_task_factory(version_type: str):
    @task
    def ver(c: Context):
        """Bump the version."""
        c.run(f"./scripts/verup.sh {version_type}")

    return ver


@task
def reqs(c: Context):
    """Upgrade requirements including pre-commit."""
    c.run("pre-commit autoupdate")
    c.run("uv lock --upgrade")


def docs_task_factory(language: str):
    @task
    def docs(c: Context):
        """Docs preview for the language specified."""
        c.run("open -a 'Google Chrome' http://127.0.0.1:8000/dinary/")
        c.run(f"scripts/build-docs.sh --copy-assets {language}")
        c.run("mkdocs serve -f docs/_mkdocs.yml")

    return docs


@task
def uv(c: Context):
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


@task
def setup(c):
    """One-time VM setup: install deps, clone repo, create services, upload creds."""
    host = _host()
    tunnel = _tunnel()

    print("=== Hardening: disable rpcbind, verify iptables ===")
    _ssh(
        c,
        "sudo systemctl stop rpcbind rpcbind.socket 2>/dev/null; "
        "sudo systemctl disable rpcbind rpcbind.socket 2>/dev/null; "
        "sudo iptables -C INPUT -i lo -j ACCEPT 2>/dev/null || "
        "sudo iptables -I INPUT 3 -i lo -j ACCEPT; "
        "sudo iptables -C INPUT -j REJECT --reject-with icmp-host-prohibited 2>/dev/null || "
        "sudo iptables -A INPUT -j REJECT --reject-with icmp-host-prohibited; "
        "sudo netfilter-persistent save 2>/dev/null; "
        "true",
    )

    print("=== Installing system packages ===")
    _ssh_sudo(c, "apt update && sudo apt install -y python3 python3-pip git curl")

    print("=== Installing uv ===")
    _ssh(c, "curl -LsSf https://astral.sh/uv/install.sh | sh")

    print("=== Cloning repo ===")
    _ssh(c, f"test -d ~/dinary-server || git clone {REPO_URL} ~/dinary-server")
    _ssh(c, "cd ~/dinary-server && git pull && source ~/.local/bin/env && uv sync --no-dev")

    print("=== Ensuring data/ directory ===")
    _ssh(c, "mkdir -p ~/dinary-server/data")

    print("=== Syncing .env to server ===")
    _sync_remote_env(c)

    print("=== Uploading credentials ===")
    _ssh(c, "mkdir -p ~/.config/gspread")
    c.run(
        f"scp ~/.config/gspread/service_account.json {host}:~/.config/gspread/service_account.json"
    )

    bind_host = "0.0.0.0" if tunnel == "none" else "127.0.0.1"
    print(f"=== Creating dinary service (bind {bind_host}) ===")
    service = DINARY_SERVICE.format(host=bind_host)
    _create_service(c, "dinary", service)

    print("=== Importing config.duckdb seed data from Google Sheets ===")
    import_config(c)

    if tunnel == "tailscale":
        _setup_tailscale(c)
    elif tunnel == "cloudflare":
        _setup_cloudflare(c)
    else:
        print("=== No tunnel configured (DINARY_TUNNEL=none) ===")

    print("=== Done! Checking health... ===")
    _ssh(c, "sleep 15 && curl -s http://localhost:8000/api/health")


@task
def deploy(c, ref="", no_restart=False):
    """Deploy latest code: git pull, sync deps, render version, restart service.

    Use --ref to deploy a specific version. Use --no-restart for the coordinated
    destructive reset flow: it skips both the post-deploy systemctl restart and
    the auto-applied config migration, because the very next step in that flow
    is `inv stop` followed by `inv import-catalog --yes` which wipes
    config.duckdb anyway.
    """
    print("=== Pre-deploy backup ===")
    ts = _dt.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = f"backups/pre-deploy-{ts}"
    os.makedirs(backup_dir, exist_ok=True)
    host = _host()
    c.run(f"scp -r {host}:~/dinary-server/data/ {backup_dir}/", warn=True)

    print("=== Deploying dinary-server ===")
    if ref:
        _ssh(
            c,
            f"cd ~/dinary-server && git fetch --tags && git checkout {ref} && source ~/.local/bin/env && uv sync --no-dev",
        )
        print(
            f"=== WARNING: Remote is in detached HEAD at '{ref}'. "
            "Future `inv deploy` without --ref will `git pull` on whatever branch is checked out. ==="
        )
    else:
        _ssh(c, "cd ~/dinary-server && git pull && source ~/.local/bin/env && uv sync --no-dev")

    print("=== Ensuring data/ directory ===")
    _ssh(c, "mkdir -p ~/dinary-server/data")

    print("=== Syncing .env to server ===")
    _sync_remote_env(c)

    if not no_restart:
        print("=== Applying config migrations ===")
        _ssh(
            c,
            "cd ~/dinary-server && source ~/.local/bin/env && "
            "uv run python -c 'from dinary.services import duckdb_repo; duckdb_repo.init_config_db(); "
            'print("Migrated config.duckdb")\'',
        )

    print("=== Building _static/ with version ===")
    _ssh(c, "cd ~/dinary-server && source ~/.local/bin/env && uv run inv build-static")

    if no_restart:
        print(
            "=== --no-restart set: SKIPPING systemctl restart and config migration. ===\n"
            "=== Next steps in the coordinated reset flow: ===\n"
            "===   inv stop                                ===\n"
            "===   inv import-catalog --yes                ===\n"
            "===   inv import-budget-all --yes             ===\n"
            "===   inv import-income-all --yes             ===\n"
            "===   inv verify-bootstrap-import-all         ===\n"
            "===   inv verify-income-equivalence-all       ===\n"
            "===   inv start                               ==="
        )
        return

    _ssh_sudo(c, "systemctl restart dinary")
    print("=== Restarted. Checking health... ===")
    _ssh(c, "sleep 5 && curl -s http://localhost:8000/api/health")


@task
def stop(c):
    """Stop the dinary systemd service (used during the coordinated reset flow)."""
    _ssh_sudo(c, "systemctl stop dinary")
    print("=== dinary stopped ===")
    _ssh_sudo(c, "systemctl is-active dinary || true")


@task
def start(c):
    """Start the dinary systemd service after a coordinated reset completes."""
    _ssh_sudo(c, "systemctl start dinary")
    print("=== dinary started, checking health... ===")
    _ssh(c, "sleep 5 && curl -s http://localhost:8000/api/health")


@task
def logs(c, follow=False, lines=100):
    """Show server logs. Use -f to follow, -l N for line count."""
    flag = "-f" if follow else f"-n {lines} --no-pager"
    c.run(f"ssh {_host()} 'sudo journalctl -u dinary {flag}'")


@task
def status(c):
    """Show server status."""
    tunnel = _tunnel()
    _ssh_sudo(c, "systemctl status dinary --no-pager")
    if tunnel == "tailscale":
        _ssh(c, "tailscale serve status")
    elif tunnel == "cloudflare":
        _ssh_sudo(c, "systemctl status cloudflared --no-pager")


@task
def ssh(c):
    """Open SSH session to the server."""
    c.run(f"ssh {_host()}", pty=True)


@task(name="import-config")
def import_config(c):
    """Import config.duckdb seed data from the configured source sheets."""
    _ssh(
        c,
        "cd ~/dinary-server && source ~/.local/bin/env && uv run python -c '"
        "from dinary.services.seed_config import seed_from_sheet; "
        "import json; print(json.dumps(seed_from_sheet()))'",
    )


@task(name="migrate-config")
def migrate_config(c):
    """Apply pending migrations to config.duckdb on the server."""
    _ssh(
        c,
        "cd ~/dinary-server && source ~/.local/bin/env && "
        "uv run python -c 'from dinary.services import duckdb_repo; "
        'duckdb_repo.init_config_db(); print("Migrated config.duckdb")\'',
    )


@task(name="migrate-budget")
def migrate_budget(c, year):
    """Apply pending migrations to a yearly budget DB on the server."""
    year_int = _coerce_year(year)
    _ssh(
        c,
        "cd ~/dinary-server && source ~/.local/bin/env && "
        f"uv run python -c 'from dinary.services import duckdb_repo; "
        f"print(duckdb_repo.init_budget_db({year_int}))'",
    )


def _require_yes(yes: bool, message: str) -> bool:
    """Gate destructive tasks behind an explicit `--yes` flag.

    Exits non-zero on the missing-flag path so CI / scripts cannot mistake a
    skipped destructive action for success. Returns True when allowed to
    proceed (so the call site reads naturally as `if not _require_yes(...)`).
    """
    if yes:
        return True
    print(message)
    print("Re-run with --yes to confirm. Aborting.")
    sys.exit(1)


def _coerce_year(year) -> int:
    """Validate and coerce a CLI `year` string to int.

    Tasks ssh `f"...({year})..."` into a remote Python invocation, so
    accepting only digits prevents shell-string injection via `--year=`.
    """
    if not year:
        return _dt.now().year
    try:
        return int(year)
    except (TypeError, ValueError):
        print(f"--year must be an integer, got {year!r}")
        sys.exit(1)


def _require_year(year) -> int:
    """Like `_coerce_year` but refuses a blank value.

    Use for destructive tasks (`import-budget`, `import-income`) where
    silently defaulting to the current year would be a footgun: an
    operator who forgets `--year=2024` would otherwise wipe the
    most-active year by accident.
    """
    if not year:
        print(
            "--year is required for destructive tasks (e.g. --year=2024).\n"
            "Refusing to default to the current year.",
        )
        sys.exit(1)
    return _coerce_year(year)


@task(name="drain-logging")
def drain_logging(c):
    """Sheet logging: drain sheet_logging_jobs for every yearly DB (on server)."""
    _ssh(
        c,
        "cd ~/dinary-server && source ~/.local/bin/env && uv run python -c '"
        "from dinary.services.sheet_logging import drain_pending; "
        "import json; print(json.dumps(drain_pending()))'",
    )


@task(name="import-catalog")
def import_catalog(c, yes=False):
    """DESTRUCTIVE: Wipe config.duckdb and re-seed the 3D classification catalog.

    Bumps `app_metadata.catalog_version` by +1 on every successful run; the
    PWA picks up the new value via GET /api/categories and POST /api/expenses.
    """
    if not _require_yes(
        yes,
        "WARNING: import-catalog will DELETE config.duckdb and re-seed it from "
        "Google Sheets. Configured import_sources are preserved and restored. "
        "All clients will be forced to refresh on next /api/categories call.",
    ):
        return
    _ssh(
        c,
        "cd ~/dinary-server && source ~/.local/bin/env && uv run python -c '"
        "from dinary.services.seed_config import rebuild_config_from_sheets; "
        "import json; print(json.dumps(rebuild_config_from_sheets()))'",
    )


@task(name="import-budget")
def import_budget(c, year="", yes=False):
    """DESTRUCTIVE: Wipe budget_YYYY.duckdb and re-import from the Google Sheet."""
    year_int = _require_year(year)
    if not _require_yes(
        yes,
        f"WARNING: import-budget will DELETE every expense in budget_{year_int}.duckdb "
        "and re-import from the Google Sheet. There is no manual-vs-import distinction "
        "anymore; nothing in the budget DB is preserved.",
    ):
        return
    _ssh(
        c,
        "cd ~/dinary-server && source ~/.local/bin/env && uv run python -c '"
        "from dinary.imports.expense_import import import_year; "
        f"import json; print(json.dumps(import_year({year_int})))'",
    )


@task(name="import-budget-all")
def import_budget_all(c, yes=False):
    """DESTRUCTIVE: Wipe and re-import every budget_YYYY.duckdb listed in import_sources.

    Iterates the years registered in `config.duckdb.import_sources` (in
    ascending order) and re-imports each one via `import_year`. Intended for
    use inside the coordinated reset flow after `import-catalog --yes`.
    """
    if not _require_yes(
        yes,
        "WARNING: import-budget-all will DELETE every budget_YYYY.duckdb "
        "registered in import_sources and re-import each year from the "
        "Google Sheet. There is no manual-vs-import distinction anymore; "
        "nothing in any budget DB is preserved.",
    ):
        return
    _ssh(
        c,
        "cd ~/dinary-server && source ~/.local/bin/env && uv run python -c '"
        "from dinary.services import duckdb_repo; "
        "from dinary.imports.expense_import import import_year; "
        "import json, duckdb; "
        "con = duckdb.connect(str(duckdb_repo.CONFIG_DB), read_only=True); "
        "years = [r[0] for r in con.execute("
        "\"SELECT year FROM import_sources WHERE year > 0 ORDER BY year\""
        ").fetchall()]; "
        "con.close(); "
        "[print(json.dumps({\"year\": y, **import_year(y)})) for y in years]'",
    )


@task(name="verify-bootstrap-import")
def verify_bootstrap_import(c, year=""):
    """Verify that bootstrap-imported budget DB reproduces sheet aggregates (on server)."""
    # `_require_year` (not `_coerce_year`) because a blank year would
    # silently verify against today's year — most likely a runtime DB with
    # `sheet_category IS NULL` everywhere — which trivially passes (`ok=true`,
    # zero exit) and gives the operator a false-green for the exact
    # equivalence step that's supposed to catch silent reset mistakes.
    year_int = _require_year(year)
    _ssh(
        c,
        "cd ~/dinary-server && source ~/.local/bin/env && uv run python -c '"
        "from dinary.imports.verify_equivalence import verify_bootstrap_import; "
        f"import json; result = verify_bootstrap_import({year_int}); "
        "print(json.dumps(result, indent=2, ensure_ascii=False)); "
        "import sys; sys.exit(0 if result[\"ok\"] else 1)'",
    )


@task(name="verify-bootstrap-import-all")
def verify_bootstrap_import_all(c):
    """Run verify-bootstrap-import for every year registered in import_sources.

    Used by the coordinated reset flow so verification covers every rebuilt
    year, not just the current calendar year. Exits non-zero if any single
    year fails the equivalence check.
    """
    _ssh(
        c,
        "cd ~/dinary-server && source ~/.local/bin/env && uv run python -c '"
        "from dinary.services import duckdb_repo; "
        "from dinary.imports.verify_equivalence import verify_bootstrap_import; "
        "import json, duckdb, sys; "
        "con = duckdb.connect(str(duckdb_repo.CONFIG_DB), read_only=True); "
        "years = [r[0] for r in con.execute("
        "\"SELECT year FROM import_sources WHERE year > 0 ORDER BY year\""
        ").fetchall()]; "
        "con.close(); "
        "results = [{**verify_bootstrap_import(y), \"year\": y} for y in years]; "
        "print(json.dumps(results, indent=2, ensure_ascii=False)); "
        "sys.exit(0 if all(r[\"ok\"] for r in results) else 1)'",
    )


@task(name="import-income")
def import_income(c, year="", yes=False):
    """DESTRUCTIVE: Wipe and re-import income for a single year from Google Sheet."""
    year_int = _require_year(year)
    if not _require_yes(
        yes,
        f"WARNING: import-income will DELETE every income row for {year_int} and re-import "
        "from Google Sheets.",
    ):
        return
    _ssh(
        c,
        "cd ~/dinary-server && source ~/.local/bin/env && uv run python -c '"
        "from dinary.imports.income_import import import_year_income; "
        f"import json; print(json.dumps(import_year_income({year_int})))'",
    )


@task(name="import-income-all")
def import_income_all(c, yes=False):
    """DESTRUCTIVE: Re-import income for all years with a registered income worksheet."""
    if not _require_yes(
        yes,
        "WARNING: import-income-all will re-import income for EVERY year with a "
        "registered income source. Existing income rows are dropped first.",
    ):
        return
    _ssh(
        c,
        "cd ~/dinary-server && source ~/.local/bin/env && uv run python -c '"
        "from dinary.services import duckdb_repo; "
        "from dinary.imports.income_import import import_year_income; "
        "import json, duckdb; "
        "con = duckdb.connect(str(duckdb_repo.CONFIG_DB), read_only=True); "
        "years = [r[0] for r in con.execute("
        "\"SELECT year FROM import_sources WHERE income_worksheet_name != \\x27\\x27 ORDER BY year\""
        ").fetchall()]; "
        "con.close(); "
        "[print(json.dumps(import_year_income(y))) for y in years]'",
    )


@task(name="verify-income-equivalence")
def verify_income_equivalence(c, year=""):
    """Verify that imported income matches the source Google Sheet (on server)."""
    # Same rationale as `verify-bootstrap-import`: a blank year that
    # silently coerces to "today" defeats the purpose of an equivalence
    # check (the operator wants to confirm the year they just rebuilt,
    # not whatever happens to be the current year).
    year_int = _require_year(year)
    _ssh(
        c,
        "cd ~/dinary-server && source ~/.local/bin/env && uv run python -c '"
        "from dinary.imports.verify_income import verify_income_equivalence; "
        f"import json; result = verify_income_equivalence({year_int}); "
        "print(json.dumps(result, indent=2, ensure_ascii=False)); "
        "import sys; sys.exit(0 if result[\"ok\"] else 1)'",
    )


@task(name="verify-income-equivalence-all")
def verify_income_equivalence_all(c):
    """Run verify-income-equivalence for every year that has an income worksheet.

    Used by the coordinated reset flow so verification covers every rebuilt
    income year. Exits non-zero if any single year fails the equivalence
    check.
    """
    _ssh(
        c,
        "cd ~/dinary-server && source ~/.local/bin/env && uv run python -c '"
        "from dinary.services import duckdb_repo; "
        "from dinary.imports.verify_income import verify_income_equivalence; "
        "import json, duckdb, sys; "
        "con = duckdb.connect(str(duckdb_repo.CONFIG_DB), read_only=True); "
        "years = [r[0] for r in con.execute("
        "\"SELECT year FROM import_sources WHERE income_worksheet_name != \\x27\\x27 ORDER BY year\""
        ").fetchall()]; "
        "con.close(); "
        "results = [{**verify_income_equivalence(y), \"year\": y} for y in years]; "
        "print(json.dumps(results, indent=2, ensure_ascii=False)); "
        "sys.exit(0 if all(r[\"ok\"] for r in results) else 1)'",
    )


@task(name="build-static")
def build_static(c):
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


@task
def backup(c):
    """Download DuckDB data files from the server to ~/Library/dinary/."""
    dest = Path.home() / "Library" / "dinary"
    ts = _dt.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = dest / ts
    backup_dir.mkdir(parents=True, exist_ok=True)
    host = _host()
    c.run(f"scp -r {host}:~/dinary-server/data/ {backup_dir}/")
    print(f"Backed up to {backup_dir}/")


@task(name="import-report-2d-3d")
def import_report_2d_3d(c, detail=False, fmt="stdout", output="", year=""):
    """Generate the 2D->3D resolution report on the server.

    Flags (all optional):
        --detail        per-row output instead of aggregated summary
        --fmt FMT       stdout (default) | csv | md
        --output PATH   override output path on the server (csv/md only;
                        relative paths are resolved against
                        ``~/dinary-server``, absolute paths are used as-is)
        --year YEAR     restrict to a single year

    For ``fmt=csv`` / ``fmt=md`` the report is written under
    ``~/dinary-server/data/reports/`` on the server (so it is part of
    ``inv backup``'s scp) and also fetched back to the local
    ``data/reports/`` directory for convenience. When ``--output`` is
    provided alongside ``--fmt csv`` / ``--fmt md`` the local copy
    lands in ``data/reports/`` regardless of the remote name.
    """
    if output and fmt not in {"csv", "md"}:
        print("--output requires --fmt csv or --fmt md (stdout has no file)")
        sys.exit(1)

    flags: list[str] = []
    if detail:
        flags.append("--detail")
    if fmt != "stdout":
        flags.extend(["--fmt", shlex.quote(fmt)])
    if output:
        flags.extend(["--output", shlex.quote(output)])
    if year:
        flags.extend(["--year", str(int(year))])
    _ssh(
        c,
        "cd ~/dinary-server && source ~/.local/bin/env && "
        f"uv run python -m dinary.imports.report_2d_3d {' '.join(flags)}",
    )

    if fmt in {"csv", "md"}:
        if output:
            # Absolute paths are used verbatim; relative paths are
            # resolved against ``~/dinary-server`` (the cwd of the
            # remote ``python -m`` call above).
            remote_path = output if output.startswith("/") else f"~/dinary-server/{output}"
        else:
            remote_path = f"~/dinary-server/data/reports/import_report_2d_3d.{fmt}"
        local_dir = Path("data") / "reports"
        local_dir.mkdir(parents=True, exist_ok=True)
        host = _host()
        c.run(f"scp {host}:{shlex.quote(remote_path)} {local_dir}/")
        print(f"Fetched {remote_path} -> {local_dir}/")


namespace = Collection.from_module(sys.modules[__name__])
for name in ALLOWED_VERSION_TYPES:
    namespace.add_task(ver_task_factory(name), name=f"ver-{name}")  # type: ignore[bad-argument-type]
for name in ALLOWED_DOC_LANGUAGES:
    namespace.add_task(docs_task_factory(name), name=f"docs-{name}")  # type: ignore[bad-argument-type]
