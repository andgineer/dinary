import base64
import os
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
Environment=DINARY_GOOGLE_SHEETS_SPREADSHEET_ID={spreadsheet_id}
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


def _sync_remote_env(c):
    """Write server-side .env with settings that CLI tasks need (idempotent)."""
    env = _env()
    spreadsheet_id = env.get("DINARY_GOOGLE_SHEETS_SPREADSHEET_ID", "")
    if not spreadsheet_id:
        print("Set DINARY_GOOGLE_SHEETS_SPREADSHEET_ID in .env")
        sys.exit(1)
    remote_env = f"DINARY_GOOGLE_SHEETS_SPREADSHEET_ID={spreadsheet_id}\n"
    _write_remote_file(c, "/home/ubuntu/dinary-server/.env", remote_env)


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
    """Run all tests (Python + JavaScript) with Allure results."""
    c.run("rm -rf allure-results")
    c.run("uv run pytest tests/ -v --alluredir=allure-results", warn=True)
    c.run("npm test", warn=True)


@task
def pre(c):
    """Run pre-commit checks."""
    c.run("pre-commit run --verbose --all-files")


@task
def setup(c):
    """One-time VM setup: install deps, clone repo, create services, upload creds."""
    env = _env()
    host = _host()
    tunnel = _tunnel()
    spreadsheet_id = env.get("DINARY_GOOGLE_SHEETS_SPREADSHEET_ID", "")
    if not spreadsheet_id:
        print("Set DINARY_GOOGLE_SHEETS_SPREADSHEET_ID in .env")
        sys.exit(1)

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
    service = DINARY_SERVICE.format(spreadsheet_id=spreadsheet_id, host=bind_host)
    _create_service(c, "dinary", service)

    if tunnel == "tailscale":
        _setup_tailscale(c)
    elif tunnel == "cloudflare":
        _setup_cloudflare(c)
    else:
        print("=== No tunnel configured (DINARY_TUNNEL=none) ===")

    print("=== Done! Checking health... ===")
    _ssh(c, "sleep 15 && curl -s http://localhost:8000/api/health")


@task
def deploy(c, ref=""):
    """Deploy latest code: git pull, sync deps, render version, restart service. Use --ref to deploy a specific version."""
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

    print("=== Applying config migrations ===")
    _ssh(
        c,
        "cd ~/dinary-server && source ~/.local/bin/env && "
        "uv run python -c 'from dinary.services import duckdb_repo; duckdb_repo.init_config_db(); "
        'print("Migrated config.duckdb")\'',
    )

    print("=== Building _static/ with version ===")
    _ssh(c, "cd ~/dinary-server && source ~/.local/bin/env && uv run inv build-static")

    _ssh_sudo(c, "systemctl restart dinary")
    print("=== Restarted. Checking health... ===")
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


@task(name="seed-config")
def seed_config(c):
    """Seed config.duckdb from Google Sheets categories (run on server)."""
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
    year = int(year)
    _ssh(
        c,
        "cd ~/dinary-server && source ~/.local/bin/env && "
        f"uv run python -c 'from dinary.services import duckdb_repo; "
        f"print(duckdb_repo.init_budget_db({year}))'",
    )


@task
def sync(c):
    """Run sheet sync for all dirty months (on server)."""
    _ssh(
        c,
        "cd ~/dinary-server && source ~/.local/bin/env && uv run python -c '"
        "from dinary.services.sync import sync_all_dirty; "
        'print(f"Synced {sync_all_dirty()} months")\'',
    )


@task(name="import-sheet")
def import_sheet(c, year="", yes=False):
    """Import a year's data from Google Sheets into DuckDB (on server).

    DESTRUCTIVE: deletes all legacy_import rows for the year before
    re-importing. Use --yes to skip the confirmation prompt.
    """
    if not year:
        year = str(_dt.now().year)
    if not yes:
        print(f"WARNING: This will DELETE all legacy_import expenses for {year}")
        print("and re-import them from the Google Sheet.")
        print("Manual expenses will NOT be affected.")
        answer = input("Type 'yes' to continue: ")
        if answer.strip().lower() != "yes":
            print("Aborted.")
            return
    _ssh(
        c,
        "cd ~/dinary-server && source ~/.local/bin/env && uv run python -c '"
        "from dinary.services.import_sheet import import_year; "
        f"import json; print(json.dumps(import_year({year})))'",
    )


@task(name="rebuild-4d-config")
def rebuild_4d_config(c):
    """DESTRUCTIVE: Wipe and rebuild config.duckdb under the 4D model from Google Sheets."""
    _ssh(
        c,
        "cd ~/dinary-server && source ~/.local/bin/env && uv run python -c '"
        "from dinary.services.seed_config import seed_from_sheet, rebuild_taxonomy; "
        "from dinary.services import duckdb_repo; "
        "import json; "
        "summary = seed_from_sheet(); "
        "con = duckdb_repo.get_config_connection(read_only=False); "
        "memberships = rebuild_taxonomy(con); con.close(); "
        'summary["taxonomy_memberships"] = memberships; '
        "print(json.dumps(summary))'",
    )


@task(name="rebuild-4d-budget")
def rebuild_4d_budget(c, year=""):
    """DESTRUCTIVE: Wipe and re-import budget_YYYY.duckdb from Google Sheet."""
    if not year:
        year = str(_dt.now().year)
    print(f"WARNING: This will DELETE ALL expenses for {year} and re-import from Google Sheets.")
    answer = input("Type 'yes' to continue: ")
    if answer.strip().lower() != "yes":
        print("Aborted.")
        return
    _ssh(
        c,
        "cd ~/dinary-server && source ~/.local/bin/env && uv run python -c '"
        "from dinary.services.import_sheet import import_year; "
        f"import json; print(json.dumps(import_year({year})))'",
    )


@task(name="verify-sheet-equivalence")
def verify_sheet_equivalence(c, year=""):
    """Verify that rebuilt DB reproduces the same Google Sheet data (on server)."""
    if not year:
        year = str(_dt.now().year)
    _ssh(
        c,
        "cd ~/dinary-server && source ~/.local/bin/env && uv run python -c '"
        "from dinary.services.verify_equivalence import verify_sheet_equivalence; "
        f"import json; result = verify_sheet_equivalence({year}); "
        "print(json.dumps(result, indent=2, ensure_ascii=False)); "
        "import sys; sys.exit(0 if result[\"ok\"] else 1)'",
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


namespace = Collection.from_module(sys.modules[__name__])
for name in ALLOWED_VERSION_TYPES:
    namespace.add_task(ver_task_factory(name), name=f"ver-{name}")  # type: ignore[bad-argument-type]
for name in ALLOWED_DOC_LANGUAGES:
    namespace.add_task(docs_task_factory(name), name=f"docs-{name}")  # type: ignore[bad-argument-type]
