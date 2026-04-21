import base64
import json as _json
import os
import re as _re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime as _dt
from pathlib import Path

from dinary.__about__ import __version__
from dinary.reports import expenses as expenses_report
from dinary.reports import income as income_report
from dinary.reports import verify_budget, verify_income
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
EnvironmentFile=/home/ubuntu/dinary-server/.deploy/.env
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

LOCAL_ENV_PATH = ".deploy/.env"
LOCAL_ENV_EXAMPLE_PATH = ".deploy.example/.env"
LOCAL_IMPORT_SOURCES_PATH = ".deploy/import_sources.json"
REMOTE_DEPLOY_DIR = "/home/ubuntu/dinary-server/.deploy"
REMOTE_ENV_PATH = f"{REMOTE_DEPLOY_DIR}/.env"
REMOTE_LEGACY_ENV_PATH = "/home/ubuntu/dinary-server/.env"
REMOTE_IMPORT_SOURCES_PATH = f"{REMOTE_DEPLOY_DIR}/import_sources.json"


def _env():
    """Read runtime env vars from ``.deploy/.env`` (the post-refactor canonical path).

    The legacy top-level ``.env`` is deliberately no longer consulted:
    it was removed in the same change that introduced
    ``.deploy/.env``, and keeping a silent fallback would make
    mis-scoped env vars hard to spot. ``.env.example`` has also been
    deleted in favour of ``.deploy.example/.env``.

    Sanity checks beyond "file exists" — the file must also be
    non-empty and not byte-equal to ``.deploy.example/.env``. Both
    failure modes are operator mistakes that would otherwise propagate
    silently: an empty ``.deploy/.env`` produces "No DINARY_* settings
    found" deep inside ``_sync_remote_env``, and an unedited copy of
    the template ships placeholder values (``ubuntu@<PUBLIC_IP>``) to
    prod, which then fail at SSH time with an opaque DNS error. Fail
    fast here with an actionable message instead.
    """
    local_path = Path(LOCAL_ENV_PATH)
    if not local_path.exists():
        print(
            f"Missing {LOCAL_ENV_PATH}. Copy {LOCAL_ENV_EXAMPLE_PATH} to {LOCAL_ENV_PATH} "
            "and fill in DINARY_DEPLOY_HOST / DINARY_TUNNEL / any sheet-logging "
            "settings you need."
        )
        sys.exit(1)
    local_bytes = local_path.read_bytes()
    if not local_bytes.strip():
        print(
            f"{LOCAL_ENV_PATH} is empty. Fill in DINARY_DEPLOY_HOST / DINARY_TUNNEL / "
            "any sheet-logging settings you need (see "
            f"{LOCAL_ENV_EXAMPLE_PATH} for the template)."
        )
        sys.exit(1)
    example_path = Path(LOCAL_ENV_EXAMPLE_PATH)
    if example_path.exists() and local_bytes == example_path.read_bytes():
        print(
            f"{LOCAL_ENV_PATH} is byte-equal to {LOCAL_ENV_EXAMPLE_PATH}; the "
            "template still has placeholder values (e.g. ubuntu@<PUBLIC_IP>) "
            "that would ship to prod and break the deploy. Edit "
            f"{LOCAL_ENV_PATH} with your real values before continuing."
        )
        sys.exit(1)
    return dotenv_values(LOCAL_ENV_PATH)


def _bind_host(tunnel: str) -> str:
    """Return the ``--host`` value ``uvicorn`` should bind to.

    Tunnel ``none`` exposes the service directly on the public
    interface; ``tailscale`` / ``cloudflare`` front it so we stay on
    loopback. Shared by ``setup`` and ``deploy`` so both paths render
    the same ``DINARY_SERVICE`` unit file.
    """
    return "0.0.0.0" if tunnel == "none" else "127.0.0.1"  # noqa: S104


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


def _ssh_capture_bytes(cmd: str) -> bytes:
    """Run *cmd* over SSH and return its stdout as raw bytes.

    Uses ``subprocess.run`` directly (not ``invoke.Context.run``) so
    the UTF-8 decode is a single end-of-stream call on the caller's
    side rather than a per-chunk ``decode(..., errors='replace')``.
    That matters because :meth:`invoke.runners.Runner.decode` splits
    decoding along read-buffer boundaries; any multi-byte character
    that lands on one becomes ``U+FFFD`` (the ``�`` replacement
    glyph), which corrupts Cyrillic text and box-drawing glyphs.

    Stderr is inherited so remote tracebacks / ``uv run`` notices
    still surface live.

    Transport shape matches the other ``_ssh*`` helpers: the command
    is base64-encoded and piped through ``base64 -d | bash`` on the
    remote, so single-quote-bearing payloads need no extra escaping.
    """
    b64 = base64.b64encode(cmd.encode()).decode()
    remote = f"echo {b64} | base64 -d | bash"
    result = subprocess.run(
        ["ssh", _host(), remote],
        stdout=subprocess.PIPE,
        check=True,
    )
    return result.stdout


def _ssh_capture(c, cmd):
    """Run *cmd* over SSH and return an ``invoke.Result``-shaped object.

    Thin adapter over :func:`_ssh_capture_bytes` exposing
    ``.stdout`` (already-decoded text) and ``.return_code``. New
    callers should prefer :func:`_ssh_capture_bytes` directly and
    decode when they actually need text.
    """
    stdout = _ssh_capture_bytes(cmd)

    class _Result:
        def __init__(self, stdout_bytes: bytes) -> None:
            self.stdout = stdout_bytes.decode("utf-8")
            self.return_code = 0

    return _Result(stdout)


def _ssh_json(c, cmd):
    """Run *cmd* on the server and return the JSON it printed to stdout.

    On parse failure the raw stdout is echoed back to local stderr
    (so a Python traceback from the remote payload stays visible)
    before raising ``RuntimeError``.
    """
    raw = _ssh_capture_bytes(cmd)
    try:
        return _json.loads(raw.decode("utf-8"))
    except _json.JSONDecodeError as exc:
        sys.stderr.write(
            "remote did not return valid JSON on stdout; raw stdout follows:\n",
        )
        sys.stderr.buffer.write(raw)
        if not raw.endswith(b"\n"):
            sys.stderr.write("\n")
        msg = (
            f"remote command failed to emit JSON: "
            f"{exc.msg} at pos {exc.pos}"
        )
        raise RuntimeError(msg) from exc


def _write_remote_file(c, path, content):
    b64 = base64.b64encode(content.encode()).decode()
    c.run(f"ssh {_host()} 'echo {b64} | base64 -d | sudo tee {path} > /dev/null'")


def _render_service(c, name, content):
    """Install / refresh a systemd unit file without restarting the service.

    Used by ``deploy`` so the unit-file content always reflects
    ``DINARY_SERVICE`` / ``CLOUDFLARED_SERVICE`` as they live in this
    repo (``git pull`` never touches ``/etc/systemd/system/*``), while
    the restart stays gated by the existing ``--no-restart`` flag
    further down the pipeline. ``daemon-reload`` here is what lets the
    follow-up ``systemctl restart`` pick up the new
    ``EnvironmentFile=`` / ``ExecStart=`` lines.
    """
    _write_remote_file(c, f"/etc/systemd/system/{name}.service", content)
    _ssh_sudo(c, "systemctl daemon-reload")
    _ssh_sudo(c, f"systemctl enable {name}")


def _create_service(c, name, content):
    """Render a systemd unit and restart it (used during ``inv setup``)."""
    _render_service(c, name, content)
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
    """Sync all DINARY_* settings from local .deploy/.env to the server (idempotent).

    Skips deploy-only keys (DINARY_DEPLOY_HOST, DINARY_TUNNEL) that are
    only meaningful on the operator's machine. Values are quoted using
    systemd's ``EnvironmentFile=`` syntax so base64-encoded credentials
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
        print(f"No DINARY_* settings found in local {LOCAL_ENV_PATH}")
        sys.exit(1)
    _ssh(c, f"mkdir -p {REMOTE_DEPLOY_DIR}")
    _write_remote_file(c, REMOTE_ENV_PATH, "".join(lines))
    _ssh_sudo(c, f"chown ubuntu:ubuntu {REMOTE_ENV_PATH} && chmod 600 {REMOTE_ENV_PATH}")


def _sync_remote_import_sources(c):
    """Upload optional ``.deploy/import_sources.json`` to the server.

    Skipped silently when the local file is absent — non-import
    deployments have no source list. Present uploads overwrite the
    remote copy so operator edits always round-trip through the
    repo-local file (single source of truth).
    """
    if not Path(LOCAL_IMPORT_SOURCES_PATH).exists():
        print(f"No {LOCAL_IMPORT_SOURCES_PATH} locally; skipping import-sources sync.")
        return
    _ssh(c, f"mkdir -p {REMOTE_DEPLOY_DIR}")
    content = Path(LOCAL_IMPORT_SOURCES_PATH).read_text(encoding="utf-8")
    _write_remote_file(c, REMOTE_IMPORT_SOURCES_PATH, content)
    _ssh_sudo(
        c,
        f"chown ubuntu:ubuntu {REMOTE_IMPORT_SOURCES_PATH} "
        f"&& chmod 600 {REMOTE_IMPORT_SOURCES_PATH}",
    )


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

    print("=== Syncing .deploy/.env to server ===")
    _sync_remote_env(c)

    print("=== Syncing .deploy/import_sources.json to server (if present) ===")
    _sync_remote_import_sources(c)

    print("=== Uploading credentials ===")
    _ssh(c, "mkdir -p ~/.config/gspread")
    c.run(
        f"scp ~/.config/gspread/service_account.json {host}:~/.config/gspread/service_account.json"
    )

    bind_host = _bind_host(tunnel)
    print(f"=== Creating dinary service (bind {bind_host}) ===")
    service = DINARY_SERVICE.format(host=bind_host)
    _create_service(c, "dinary", service)

    print("=== Bootstrapping runtime catalog (no Google Sheets required) ===")
    bootstrap_catalog(c)

    if Path(LOCAL_IMPORT_SOURCES_PATH).exists():
        print("=== Importing catalog from Google Sheets (import_sources.json present) ===")
        import_config(c)
    else:
        print(
            "=== Skipping import-config (no .deploy/import_sources.json locally). ===\n"
            "=== Runtime catalog is populated; /api/expenses will work. ==="
        )

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
    reset flow: it skips both the post-deploy systemctl restart and the
    auto-applied schema migration, because the very next step in that flow is
    `inv stop` followed by `rm -f ~/dinary-server/data/*.duckdb` +
    `inv migrate` + `inv import-catalog --yes` which rebuilds the single DB
    anyway.

    Pipeline (ordering matters — see ``.plans/architecture.md`` and the
    original inline-fk-drop-and-reset-db plan for the constraints):

    * ``_sync_remote_env`` writes to
      ``~/dinary-server/.deploy/.env`` via ``sudo tee``, which does
      not ``mkdir -p`` — ``_sync_remote_env`` itself now runs
      ``mkdir -p`` first.
    * We re-render ``/etc/systemd/system/dinary.service`` every
      deploy so ``EnvironmentFile=`` always matches the canonical
      ``.deploy/.env`` path. ``git pull`` never touches the unit
      file, so without this the old unit (pointing at the legacy
      top-level ``.env``) would survive the deploy and the
      post-deploy ``systemctl restart`` would start dinary with an
      unresolved ``EnvironmentFile``.
    * Legacy ``~/dinary-server/.env`` is removed *after* the unit
      has been rewritten so systemd is never left with a dangling
      ``EnvironmentFile`` pointer.
    """
    print("=== Pre-deploy backup ===")
    ts = _dt.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = f"backups/pre-deploy-{ts}"
    os.makedirs(backup_dir, exist_ok=True)
    host = _host()
    tunnel = _tunnel()
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

    print("=== Syncing .deploy/.env to server ===")
    _sync_remote_env(c)

    print("=== Syncing .deploy/import_sources.json to server (if present) ===")
    _sync_remote_import_sources(c)

    bind_host = _bind_host(tunnel)
    print(f"=== Re-rendering dinary systemd unit (bind {bind_host}) ===")
    _render_service(c, "dinary", DINARY_SERVICE.format(host=bind_host))

    print("=== Cleaning up legacy ~/dinary-server/.env (if present) ===")
    _ssh(c, f"rm -f {REMOTE_LEGACY_ENV_PATH}")

    if not no_restart:
        print("=== Applying schema migrations ===")
        _ssh(
            c,
            "cd ~/dinary-server && source ~/.local/bin/env && "
            "uv run python -c 'from dinary.services import duckdb_repo; duckdb_repo.init_db(); "
            'print("Migrated data/dinary.duckdb")\'',
        )
        print("=== Bootstrapping runtime catalog (idempotent) ===")
        bootstrap_catalog(c)

    print("=== Building _static/ with version ===")
    _ssh(c, "cd ~/dinary-server && source ~/.local/bin/env && uv run inv build-static")

    if no_restart:
        print(
            "=== --no-restart set: SKIPPING systemctl restart and schema migration. ===\n"
            "=== Next steps in the coordinated reset flow:   ===\n"
            "===   inv stop                                  ===\n"
            "===   ssh $HOST 'rm -f ~/dinary-server/data/*.duckdb'  ===\n"
            "===   inv migrate                               ===\n"
            "===   inv import-catalog --yes                  ===\n"
            "===   inv import-budget-all --yes               ===\n"
            "===   inv import-income-all --yes               ===\n"
            "===   inv verify-bootstrap-import-all           ===\n"
            "===   inv verify-income-equivalence-all         ===\n"
            "===   inv start                                 ==="
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


@task(name="bootstrap-catalog")
def bootstrap_catalog(c):
    """Populate runtime catalog (groups/categories/tags/events) from hardcoded taxonomy.

    Non-destructive and idempotent. Required for every deployment —
    non-import users rely on this as their only catalog-population
    path, and the import flow (``inv import-catalog``) implicitly
    re-runs the same logic as its first step.

    Does NOT touch Google Sheets, ``import_mapping``, or
    ``sheet_mapping``. Does NOT bump ``catalog_version`` unless the
    hardcoded taxonomy actually changed from what's already on disk.
    """
    _ssh(
        c,
        "cd ~/dinary-server && source ~/.local/bin/env && uv run python -c '"
        "from dinary.services.seed_config import bootstrap_catalog; "
        "import json; print(json.dumps(bootstrap_catalog()))'",
    )


@task(name="import-config")
def import_config(c):
    """Seed the catalog from the configured source sheets (non-destructive).

    Requires ``.deploy/import_sources.json`` to exist locally AND on
    the server (uploaded via ``_sync_remote_import_sources`` during
    ``inv deploy`` / ``inv setup``). Fails loud with a pointer to the
    repo-root ``imports/`` directory when the file is missing or empty.
    """
    _ssh(
        c,
        "cd ~/dinary-server && source ~/.local/bin/env && uv run python -c '"
        "from dinary.imports.seed import seed_from_sheet; "
        "import json; print(json.dumps(seed_from_sheet()))'",
    )


@task(name="migrate")
def migrate(c):
    """Apply pending migrations to ``data/dinary.duckdb`` on the server."""
    _ssh(
        c,
        "cd ~/dinary-server && source ~/.local/bin/env && "
        "uv run python -c 'from dinary.services import duckdb_repo; "
        'duckdb_repo.init_db(); print("Migrated data/dinary.duckdb")\'',
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


@task(name="import-catalog")
def import_catalog(c, yes=False):
    """FK-safe in-place catalog sync: re-seed taxonomy without deleting the DB.

    Never deletes ``data/dinary.duckdb``. Ledger tables
    (``expenses``/``expense_tags``/``sheet_logging_jobs``/``income``)
    stay intact; catalog rows are upserted by natural key so existing
    integer ids are preserved. Vocabulary no longer present in the
    seed files is marked ``is_active=FALSE``. The ``import_mapping``
    table is rebuilt from scratch against the current active taxonomy
    ids; the runtime ``sheet_mapping`` table is owned by the
    ``map`` worksheet tab and is not touched here.

    Bumps ``app_metadata.catalog_version`` by +1 only when the
    rebuild observably changed the catalog (hash-gated); the PWA
    picks up the new value via ``GET /api/catalog`` and
    ``POST /api/expenses``.
    """
    if not _require_yes(
        yes,
        "WARNING: import-catalog will re-sync the taxonomy from Google Sheets. "
        "Existing ledger data (expenses, tags, sheet logging queue, income) is "
        "preserved. Vocabulary not present in the seed files will be marked "
        "inactive. The ``import_mapping`` table will be rebuilt. PWA clients "
        "will be forced to refresh on next /api/catalog call.",
    ):
        return
    _ssh(
        c,
        "cd ~/dinary-server && source ~/.local/bin/env && uv run python -c '"
        "from dinary.imports.seed import rebuild_config_from_sheets; "
        "import json; print(json.dumps(rebuild_config_from_sheets()))'",
    )


@task(name="import-budget")
def import_budget(c, year="", yes=False):
    """DESTRUCTIVE: Delete every expense for the given year and re-import from Sheet.

    Operates on the single ``data/dinary.duckdb`` file: the year's rows in
    ``expenses``/``expense_tags``/``sheet_logging_jobs`` are removed and
    re-imported from Google Sheets. Other years stay untouched.
    """
    year_int = _require_year(year)
    if not _require_yes(
        yes,
        f"WARNING: import-budget will DELETE every expense for {year_int} in "
        "data/dinary.duckdb and re-import from the Google Sheet. Other years "
        "are untouched. There is no manual-vs-import distinction anymore; "
        "nothing in the targeted year is preserved.",
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
    """DESTRUCTIVE: Re-import every year registered in ``.deploy/import_sources.json``.

    Iterates positive-year entries from the server-side
    ``.deploy/import_sources.json`` (in ascending order) and
    re-imports each one via ``import_year``, which deletes just that
    year's expense rows before re-importing. Intended for use inside
    the coordinated reset flow after ``import-catalog --yes``.
    """
    if not _require_yes(
        yes,
        "WARNING: import-budget-all will DELETE every expense row for every "
        "year registered in .deploy/import_sources.json and re-import each year "
        "from the Google Sheet. Other ledger data (income, sheet logging queue) "
        "is preserved; within each affected year nothing is preserved.",
    ):
        return
    _ssh(
        c,
        "cd ~/dinary-server && source ~/.local/bin/env && uv run python -c '"
        "from dinary.config import read_import_sources; "
        "from dinary.imports.expense_import import import_year; "
        "import json; "
        "years = sorted(r.year for r in read_import_sources() if r.year > 0); "
        "[print(json.dumps({\"year\": y, **import_year(y)})) for y in years]'",
    )


@task(name="verify-bootstrap-import")
def verify_bootstrap_import(c, year="", json=False):  # noqa: A002
    """Verify that bootstrap-imported budget DB reproduces sheet aggregates (on server).

    Renders a rich summary panel with drill-down tables for
    missing / extra / amount / comment diffs. Exits non-zero iff
    ``ok=False`` on the verifier payload. Pass ``--json`` to emit
    the raw ``json.dumps(..., indent=2)`` blob instead of the rich
    view (back-compat for scripted consumers).
    """
    # `_require_year` (not `_coerce_year`) because a blank year would
    # silently verify against today's year — most likely a runtime DB with
    # `sheet_category IS NULL` everywhere — which trivially passes (`ok=true`,
    # zero exit) and gives the operator a false-green for the exact
    # equivalence step that's supposed to catch silent reset mistakes.
    year_int = _require_year(year)
    # The remote payload prints *just* the JSON — we derive the
    # exit code locally from the parsed ``ok`` field so SSH exit
    # status and renderer status cannot drift out of sync.
    result = _ssh_json(
        c,
        "cd ~/dinary-server && source ~/.local/bin/env && uv run python -c '"
        "from dinary.imports.verify_equivalence import verify_bootstrap_import; "
        f"import json; print(json.dumps(verify_bootstrap_import({year_int}), "
        "indent=2, ensure_ascii=False))'",
    )
    if json:
        verify_budget.print_json(result)
    else:
        verify_budget.render_single(result)
    sys.exit(verify_budget.exit_code_for_single(result))


@task(name="verify-bootstrap-import-all")
def verify_bootstrap_import_all(c, json=False):  # noqa: A002
    """Run verify-bootstrap-import for every positive-year entry in ``.deploy/import_sources.json``.

    Used by the coordinated reset flow so verification covers every rebuilt
    year, not just the current calendar year. Renders a rich summary
    table across all years with per-failing-year drill-downs. Exits
    non-zero if any single year fails. Pass ``--json`` to emit the
    raw JSON array (back-compat for scripted consumers).
    """
    results = _ssh_json(
        c,
        "cd ~/dinary-server && source ~/.local/bin/env && uv run python -c '"
        "from dinary.config import read_import_sources; "
        "from dinary.imports.verify_equivalence import verify_bootstrap_import; "
        "import json; "
        "years = sorted(r.year for r in read_import_sources() if r.year > 0); "
        "results = [{**verify_bootstrap_import(y), \"year\": y} for y in years]; "
        "print(json.dumps(results, indent=2, ensure_ascii=False))'",
    )
    if json:
        verify_budget.print_json(results)
    else:
        verify_budget.render_batch(results)
    sys.exit(verify_budget.exit_code_for_batch(results))


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
        "from dinary.config import read_import_sources; "
        "from dinary.imports.income_import import import_year_income; "
        "import json; "
        "years = sorted("
        "r.year for r in read_import_sources() "
        "if r.year > 0 and r.income_worksheet_name"
        "); "
        "[print(json.dumps(import_year_income(y))) for y in years]'",
    )


@task(name="verify-income-equivalence")
def verify_income_equivalence(c, year="", json=False):  # noqa: A002
    """Verify that imported income matches the source Google Sheet (on server).

    Renders a rich summary panel with per-month diff table when
    diffs are present. Exits non-zero on ``ok=False`` (including
    the early-exit error branches, e.g. missing ``import_sources``
    entry). Pass ``--json`` for the raw JSON escape hatch.
    """
    # Same rationale as `verify-bootstrap-import`: a blank year that
    # silently coerces to "today" defeats the purpose of an equivalence
    # check (the operator wants to confirm the year they just rebuilt,
    # not whatever happens to be the current year).
    year_int = _require_year(year)
    result = _ssh_json(
        c,
        "cd ~/dinary-server && source ~/.local/bin/env && uv run python -c '"
        "from dinary.imports.verify_income import verify_income_equivalence; "
        f"import json; print(json.dumps(verify_income_equivalence({year_int}), "
        "indent=2, ensure_ascii=False))'",
    )
    if json:
        verify_income.print_json(result)
    else:
        verify_income.render_single(result)
    sys.exit(verify_income.exit_code_for_single(result))


@task(name="verify-income-equivalence-all")
def verify_income_equivalence_all(c, json=False):  # noqa: A002
    """Run verify-income-equivalence for every year that has an income worksheet.

    Used by the coordinated reset flow so verification covers every rebuilt
    income year. Renders a rich summary table with per-failing-year
    drill-downs. Exits non-zero if any single year fails. Pass
    ``--json`` for the raw JSON array (back-compat for scripted
    consumers).
    """
    results = _ssh_json(
        c,
        "cd ~/dinary-server && source ~/.local/bin/env && uv run python -c '"
        "from dinary.config import read_import_sources; "
        "from dinary.imports.verify_income import verify_income_equivalence; "
        "import json; "
        "years = sorted("
        "r.year for r in read_import_sources() "
        "if r.year > 0 and r.income_worksheet_name"
        "); "
        "results = [{**verify_income_equivalence(y), \"year\": y} for y in years]; "
        "print(json.dumps(results, indent=2, ensure_ascii=False))'",
    )
    if json:
        verify_income.print_json(results)
    else:
        verify_income.render_batch(results)
    sys.exit(verify_income.exit_code_for_batch(results))


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


def _render_2d3d_locally(raw: bytes, *, as_csv: bool) -> None:
    """Render a JSON envelope produced by ``dinary.imports.report_2d_3d --json``.

    The envelope carries both the row shape discriminator (``detail``)
    and the column order, so the caller doesn't need to know whether
    summary or detail rows came back.
    """
    from dinary.imports import report_2d_3d as report_module

    payload = _json.loads(raw.decode("utf-8"))
    rows = report_module.rows_from_json(payload)
    columns = payload["columns"]
    if as_csv:
        report_module.render_csv(rows, columns, output=sys.stdout)
    else:
        report_module.render_rich(rows, columns, output=sys.stdout)


@task(name="import-report-2d-3d")
def import_report_2d_3d(
    c,
    detail=False,
    csv=False,  # noqa: A002
    json=False,
    year="",
    remote=False,
):
    """Show the 2D->3D resolution report over the imported sheets.

    Flags (all optional):
        --detail   per-row output instead of aggregated summary
        --csv      emit CSV to stdout instead of a rich table
        --json     emit a JSON envelope to stdout (mutex with --csv)
        --year Y   restrict to a single calendar year
        --remote   run the report on the server over SSH (default:
                   runs locally against ``data/dinary.duckdb``)

    ``--remote`` uses the same JSON-over-SSH transport as
    ``inv report-*`` (see :func:`_run_report_module`): the server
    always emits ``--json`` against a snapshot of the live DB and
    the local process renders. That is the only way to keep Cyrillic
    / box-drawing glyphs intact across the SSH pipe.
    """
    if csv and json:
        print("--csv and --json are mutually exclusive", file=sys.stderr)
        sys.exit(1)

    def _build_flags(*, force_json: bool) -> list[str]:
        flags: list[str] = []
        if detail:
            flags.append("--detail")
        if force_json or json:
            flags.append("--json")
        elif csv:
            flags.append("--csv")
        if year:
            flags.extend(["--year", str(int(year))])
        return flags

    if not remote:
        cmd = "uv run python -m dinary.imports.report_2d_3d"
        local_flags = _build_flags(force_json=False)
        if local_flags:
            cmd = f"{cmd} {' '.join(local_flags)}"
        c.run(cmd)
        return

    remote_cmd = (
        "cd ~/dinary-server && source ~/.local/bin/env && "
        f"uv run python -m dinary.imports.report_2d_3d {' '.join(_build_flags(force_json=True))}"
    )
    raw = _ssh_capture_bytes(remote_cmd)

    # ``--json --remote`` is the pipe-into-jq case: forward the
    # server's bytes verbatim so stdout is exactly what the remote
    # emitted.
    if json:
        sys.stdout.buffer.write(raw)
        return

    _render_2d3d_locally(raw, as_csv=csv)


#: Remote path of the live DuckDB file the server process keeps
#: exclusively locked (single-writer DuckDB 1.x).
_REMOTE_DB_PATH = "/home/ubuntu/dinary-server/data/dinary.duckdb"


def _remote_report_cmd(module: str, flags: list[str]) -> str:
    """Build the remote shell command for ``inv report-<module> --remote``.

    DuckDB 1.x holds a single-writer exclusive file lock on the
    primary DB file, so a second process — even one opened
    ``read_only=True`` — cannot attach while the server is up
    (``IOException: Could not set lock on file ...``). The workaround:
    ``cp``-snapshot the primary file (and its ``.wal`` sidecar when
    present) into ``/tmp``, point ``DINARY_DATA_PATH`` at the
    snapshot, and run the read-only report module against that
    isolated copy. The snapshot is torn down on every exit path via
    ``trap`` so a failed report never leaks a multi-hundred-MB file.

    Snapshot staleness: the ``cp`` captures whatever bytes are on
    disk at that moment. A write mid-checkpoint can leave the main
    file and WAL slightly out of sync, but DuckDB replays the WAL
    on open and converges to the latest *committed* state — so the
    operator sees a consistent point-in-time view, at most a single
    uncommitted transaction stale. Acceptable for operator-triggered
    read-only reporting.

    Unique per-invocation snapshot path: ``$$`` expands to the
    remote shell's PID so two parallel ``inv report-*`` runs from
    separate laptops cannot stomp on the same ``/tmp`` file.
    """
    report = f"uv run python -m dinary.reports.{module}"
    if flags:
        report = f"{report} {' '.join(flags)}"
    # ``set -e`` propagates a failed ``cp`` as a non-zero exit so the
    # operator sees it rather than silently running the report
    # against a stale / missing snapshot. The trap is registered
    # *before* the copy so an interrupt between ``cp`` and ``trap``
    # cannot leak the snapshot.
    return (
        "set -e; "
        "SNAP=/tmp/dinary-report-snapshot-$$.duckdb; "
        'trap "rm -f ${SNAP} ${SNAP}.wal" EXIT; '
        f"cp {_REMOTE_DB_PATH} ${{SNAP}}; "
        f"cp {_REMOTE_DB_PATH}.wal ${{SNAP}}.wal 2>/dev/null || true; "
        "cd ~/dinary-server && source ~/.local/bin/env && "
        f"DINARY_DATA_PATH=${{SNAP}} {report}"
    )


def _extract_format_flags(flags: list[str]) -> tuple[bool, bool, list[str]]:
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


def _extract_year_month(filter_flags: list[str]) -> tuple[int | None, tuple[int, int] | None]:
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


def _run_report_module(c, module: str, flags: list[str], *, remote: bool) -> None:
    """Dispatch a ``dinary.reports.<module>`` run locally or over SSH.

    Both modes follow the same shape: fetch rows → render locally.

    * Local: ``uv run python -m dinary.reports.<module> <flags>`` —
      a single process fetches from ``data/dinary.duckdb`` and
      renders.
    * Remote: the same module runs on the server in ``--json`` mode
      via the SSH snapshot wrapper (see :func:`_remote_report_cmd`
      for why the DuckDB snapshot is needed), the JSON bytes come
      back through :func:`_ssh_capture_bytes`, and the module's
      ``render`` runs on the local terminal.

    JSON is the wire format so stdout over SSH only ever carries
    structured data: a single end-of-stream UTF-8 decode on the
    client keeps Cyrillic and box-drawing glyphs intact regardless
    of how the SSH transport chunks the stream. The local side then
    picks any renderer (rich, csv, or raw JSON passthrough).
    """
    if not remote:
        cmd = f"uv run python -m dinary.reports.{module}"
        if flags:
            cmd = f"{cmd} {' '.join(flags)}"
        c.run(cmd)
        return

    as_csv, as_json, filter_flags = _extract_format_flags(flags)

    # The remote always runs in JSON mode. ``--csv`` / ``--json``
    # the operator passed are handled locally (below).
    remote_flags = [*filter_flags, "--json"]
    raw = _ssh_capture_bytes(_remote_report_cmd(module, remote_flags))

    # ``--json --remote`` is the "pipe into jq" case: forward the
    # server's bytes verbatim so stdout is exactly what the remote
    # emitted (no key-order / whitespace round-trip through
    # ``rows_from_json`` + ``render_json``).
    if as_json:
        sys.stdout.buffer.write(raw)
        return

    payload = _json.loads(raw.decode("utf-8"))

    # Per-module dispatch is explicit so pyrefly can narrow the row
    # type from ``rows_from_json`` into ``render``; a module-indexed
    # dict would widen to the union of row types and fail the check.
    if module == "income":
        income_rows = income_report.rows_from_json(payload)
        income_report.render(income_rows, as_csv=as_csv, stream=sys.stdout)
    elif module == "expenses":
        expense_rows = expenses_report.rows_from_json(payload)
        year, month = _extract_year_month(filter_flags)
        expenses_report.render(
            expense_rows,
            year=year,
            month=month,
            as_csv=as_csv,
            stream=sys.stdout,
        )
    else:
        msg = f"unknown report module: {module!r}"
        raise ValueError(msg)


@task(name="report-expenses")
def report_expenses(c, year="", month="", csv=False, remote=False):  # noqa: A002
    """Show expenses aggregated by unique (category, event, tags) coord.

    Flags (all optional):
        --year YYYY        restrict to a single calendar year
        --month YYYY-MM    restrict to a single month (mutex with --year)
        --csv              emit CSV to stdout instead of a rich table
        --remote           query the production DB over SSH. Default
                           runs locally against ``data/dinary.duckdb``
                           — useful after ``inv backup`` or during
                           local development.

    The aggregation key is the project's 3D coord: the expense's
    category name, its event name (blank when the expense has no
    event), and the deterministic join of its tag names. Rows sort
    by descending total so the biggest spend lines surface at the top.
    """
    if year and month:
        print("--year and --month are mutually exclusive", file=sys.stderr)
        sys.exit(1)

    flags: list[str] = []
    if year:
        flags.extend(["--year", str(int(year))])
    if month:
        flags.extend(["--month", shlex.quote(month)])
    if csv:
        flags.append("--csv")
    _run_report_module(c, "expenses", flags, remote=remote)


@task(name="report-income")
def report_income(c, csv=False, remote=False):  # noqa: A002
    """Show income aggregated by year.

    Flags (all optional):
        --csv      emit CSV to stdout instead of a rich table
        --remote   query the production DB over SSH. Default runs
                   locally against ``data/dinary.duckdb``.

    One row per calendar year, with per-year total, count of
    months-with-data, and average per data-month.
    """
    flags: list[str] = []
    if csv:
        flags.append("--csv")
    _run_report_module(c, "income", flags, remote=remote)


namespace = Collection.from_module(sys.modules[__name__])
for name in ALLOWED_VERSION_TYPES:
    namespace.add_task(ver_task_factory(name), name=f"ver-{name}")  # type: ignore[bad-argument-type]
for name in ALLOWED_DOC_LANGUAGES:
    namespace.add_task(docs_task_factory(name), name=f"docs-{name}")  # type: ignore[bad-argument-type]
