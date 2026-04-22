import base64
import json as _json
import re as _re
import shlex
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime as _dt
from pathlib import Path

from dinary.__about__ import __version__
from dinary.reports import expenses as expenses_report
from dinary.reports import income as income_report
from dinary.reports import verify_budget, verify_income
from dinary.tools import sql as sql_module
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

REPO_URL = "https://github.com/andgineer/dinary.git"

DINARY_SERVICE = """\
[Unit]
Description=dinary
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/dinary
EnvironmentFile=/home/ubuntu/dinary/.deploy/.env
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

# systemd unit that runs the Litestream replicator sidecar on VM 1.
# ``User=ubuntu`` matches the app service so the sidecar shares the
# ``data/`` file ACL and can read WAL segments without sudo. The config
# lives at ``/etc/litestream.yml`` (uploaded by ``inv litestream-setup``)
# because that is Litestream's default search path and operators editing
# by hand find it where the upstream docs say it should be.
LITESTREAM_SERVICE = """\
[Unit]
Description=Litestream replicator for dinary
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
ExecStart=/usr/bin/litestream replicate -config /etc/litestream.yml
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""

VALID_TUNNELS = ("tailscale", "cloudflare", "none")

LOCAL_ENV_PATH = ".deploy/.env"
LOCAL_ENV_EXAMPLE_PATH = ".deploy.example/.env"
LOCAL_IMPORT_SOURCES_PATH = ".deploy/import_sources.json"
LOCAL_LITESTREAM_CONFIG_PATH = ".deploy/litestream.yml"
LOCAL_LITESTREAM_EXAMPLE_PATH = ".deploy.example/litestream.yml"
REMOTE_DEPLOY_DIR = "/home/ubuntu/dinary/.deploy"
REMOTE_ENV_PATH = f"{REMOTE_DEPLOY_DIR}/.env"
REMOTE_LEGACY_ENV_PATH = "/home/ubuntu/dinary/.env"
REMOTE_IMPORT_SOURCES_PATH = f"{REMOTE_DEPLOY_DIR}/import_sources.json"
REMOTE_LITESTREAM_CONFIG_PATH = "/etc/litestream.yml"

# Pinned Litestream release we install on VM 1. Keeping this as a
# module-level constant — rather than inlined into the shell script
# below — lets tests pin the exact version independently of the
# asset-URL construction, and makes a future upgrade a one-line
# diff that cannot get out of sync with the asset filenames.
LITESTREAM_VERSION = "0.5.1"


def _litestream_install_script(version: str = LITESTREAM_VERSION) -> str:
    """Return the shell snippet that installs Litestream on the VM.

    Extracted as a pure helper so ``tests/test_tasks.py`` can assert
    the arch-detection branches without booting an SSH session.

    The filename suffix contract comes from the upstream release page:
    assets are published as ``litestream-<version>-linux-x86_64.deb``
    and ``litestream-<version>-linux-arm64.deb``. We map ``uname -m``
    outputs (``x86_64`` / ``amd64`` on Intel-style hosts, ``aarch64``
    / ``arm64`` on Ampere) to those canonical suffixes. Other
    architectures exit 1 with an actionable error instead of silently
    downloading a non-existent asset.
    """
    base_url = (
        f"https://github.com/benbjohnson/litestream/releases/download/v{version}"
    )
    return (
        "set -e; "
        "if ! command -v litestream >/dev/null; then "
        'ARCH="$(uname -m)"; '
        'case "$ARCH" in '
        f"  x86_64|amd64) ASSET=litestream-{version}-linux-x86_64.deb ;; "
        f"  aarch64|arm64) ASSET=litestream-{version}-linux-arm64.deb ;; "
        f'  *) echo "Unsupported arch $ARCH for litestream {version}" >&2; exit 1 ;; '
        "esac; "
        "TMP=$(mktemp -d); "
        "curl -fsSL -o $TMP/litestream.deb "
        f'"{base_url}/$ASSET"; '
        "sudo dpkg -i $TMP/litestream.deb; "
        "rm -rf $TMP; "
        "fi"
    )


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


@task(help={
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
})
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
        # Kill any orphaned local uvicorn that would otherwise hold
        # the SQLite WAL connection while we try to remove the file.
        # ``pkill -f`` exits 1 when nothing matches, which is not an
        # error condition for us; ``check=False`` absorbs that.
        subprocess.run(
            ["pkill", "-f", "uvicorn dinary"],
            check=False,
        )
        # Remove the SQLite DB and its WAL sidecars. The three-file
        # set (``.db`` + ``-wal`` + ``-shm``) is the canonical WAL
        # footprint; leaving the sidecars around after deleting the
        # main file would confuse a subsequent connect into
        # reconstructing a stale partial state.
        for fn in (
            "data/dinary.db",
            "data/dinary.db-wal",
            "data/dinary.db-shm",
        ):
            p = Path(fn)
            if p.exists():
                p.unlink()
                print(f"Removed {p}")
        # ``bootstrap_catalog()`` internally calls ``init_db()`` so
        # migrations run as part of seeding — no separate migrate
        # call needed. It's also idempotent, so running ``--reset``
        # on an already-seeded DB is safe (it just no-ops after the
        # file rm path above wipes it).
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
    _ssh_sudo(
        c,
        "apt update && sudo apt install -y python3 python3-pip git curl sqlite3",
    )

    print("=== Provisioning swap file ===")
    setup_swap(c)

    print("=== Installing uv ===")
    _ssh(c, "curl -LsSf https://astral.sh/uv/install.sh | sh")

    print("=== Cloning repo ===")
    _ssh(c, f"test -d ~/dinary || git clone {REPO_URL} ~/dinary")
    _ssh(c, "cd ~/dinary && git pull && source ~/.local/bin/env && uv sync --no-dev")

    print("=== Ensuring data/ directory ===")
    _ssh(c, "mkdir -p ~/dinary/data")

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

    # Litestream bootstrap is intentionally NOT auto-invoked here. The
    # sidecar requires an already-reachable SFTP replica host whose
    # ``authorized_keys`` accepts VM 1's ed25519 public key — a
    # cross-host trust relationship we cannot set up from this
    # machine. Operators opt in explicitly with ``inv litestream-setup``
    # once that prerequisite is in place. See
    # ``docs/src/en/operations.md`` for the end-to-end bootstrap.
    if Path(LOCAL_LITESTREAM_CONFIG_PATH).exists():
        print(
            "=== .deploy/litestream.yml present — run `inv litestream-setup` ===\n"
            "=== manually once the SFTP replica host trusts VM 1's ssh key. ==="
        )
    else:
        print(
            "=== Skipping Litestream (no .deploy/litestream.yml locally). ===\n"
            "=== Copy .deploy.example/litestream.yml and run `inv litestream-setup` "
            "when you have an SFTP replica target. ==="
        )

    print("=== Done! Checking health... ===")
    _ssh(c, "sleep 15 && curl -s http://localhost:8000/api/health")


def _build_setup_swap_script(*, size_gb: int) -> str:
    """Emit the shell script that idempotently installs ``/swapfile``.

    Kept separate from :func:`setup_swap` so tests can pin the shape
    of the emitted script (fallocate size, idempotency guard, fstab
    dedup) without mocking SSH.

    Uses ``sudo bash <<'HEREDOC'`` so every command inside the
    block runs as root without nested-quote gymnastics. The
    heredoc is quoted (``<<'EOF'``) so ``$FSTAB_LINE`` stays
    literal for the remote shell instead of being expanded locally
    to an empty string.
    """
    if size_gb <= 0:
        msg = f"size_gb must be a positive integer, got {size_gb!r}"
        raise ValueError(msg)
    return (
        "sudo bash <<'DINARY_SWAP_EOF'\n"
        "set -euo pipefail\n"
        "if swapon --show=NAME --noheadings 2>/dev/null | grep -qx /swapfile; then\n"
        '  echo "/swapfile already active, skipping allocation"\n'
        "else\n"
        f"  fallocate -l {size_gb}G /swapfile\n"
        "  chmod 600 /swapfile\n"
        "  mkswap /swapfile\n"
        "  swapon /swapfile\n"
        "fi\n"
        "FSTAB_LINE='/swapfile none swap sw 0 0'\n"
        'grep -qxF "$FSTAB_LINE" /etc/fstab || echo "$FSTAB_LINE" >> /etc/fstab\n'
        "swapon --show\n"
        "free -h\n"
        "DINARY_SWAP_EOF\n"
    )


@task(name="setup-swap")
def setup_swap(c, size_gb=1):
    """Provision a persistent ``/swapfile`` on the server (idempotent).

    Oracle Cloud Always Free VMs ship with zero swap and ~1 GiB RAM,
    so a transient memory spike (bulk import, ``uv sync`` on a fat
    lockfile, a cloudflared update) can OOM-kill ``dinary`` even
    with 40 GB of idle disk. This task allocates ``/swapfile``,
    activates it, and wires it into ``/etc/fstab`` so the swap
    survives reboots.

    Re-running is safe: if ``/swapfile`` is already active the
    allocation is skipped, and the fstab line is appended only when
    not already present. Changing ``--size-gb`` on a re-run does
    **not** silently resize — operators must ``swapoff /swapfile``
    and ``rm /swapfile`` manually first, otherwise the system would
    briefly go to zero swap under load during the resize.

    Flags:
        --size-gb N   swap file size in gigabytes (default 1).
    """
    size = int(size_gb)
    script = _build_setup_swap_script(size_gb=size)
    _ssh(c, script)


@task
def deploy(c, ref="", no_restart=False):
    """Deploy latest code: git pull, sync deps, render version, restart service.

    Use --ref to deploy a specific version. Use --no-restart for the coordinated
    reset flow: it skips both the post-deploy systemctl restart and the
    auto-applied schema migration, because the very next step in that flow is
    `inv stop` followed by `rm -f ~/dinary/data/dinary.db*` +
    `inv migrate` + `inv import-catalog --yes` which rebuilds the single DB
    anyway.

    Pipeline (ordering matters — see ``.plans/architecture.md`` and the
    original inline-fk-drop-and-reset-db plan for the constraints):

    * Pre-deploy safety-net backup uses ``sqlite3 .backup`` on the
      server (same mechanism as ``inv backup``) to take a
      transactionally consistent snapshot of ``dinary.db``. A raw
      ``scp -r data/`` of the live WAL triple
      (``.db`` + ``-wal`` + ``-shm``) would not produce a consistent
      image because in-flight WAL frames could be mid-write while
      the files are being copied.
    * ``_sync_remote_env`` writes to
      ``~/dinary/.deploy/.env`` via ``sudo tee``, which does
      not ``mkdir -p`` — ``_sync_remote_env`` itself now runs
      ``mkdir -p`` first.
    * We re-render ``/etc/systemd/system/dinary.service`` every
      deploy so ``EnvironmentFile=`` always matches the canonical
      ``.deploy/.env`` path. ``git pull`` never touches the unit
      file, so without this the old unit (pointing at the legacy
      top-level ``.env``) would survive the deploy and the
      post-deploy ``systemctl restart`` would start dinary with an
      unresolved ``EnvironmentFile``.
    * Legacy ``~/dinary/.env`` is removed *after* the unit
      has been rewritten so systemd is never left with a dangling
      ``EnvironmentFile`` pointer.
    """
    print("=== Pre-deploy backup ===")
    ts = _dt.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = Path(f"backups/pre-deploy-{ts}")
    backup_dir.mkdir(parents=True, exist_ok=True)
    host = _host()
    tunnel = _tunnel()
    # Stream a transactionally consistent SQLite snapshot from the
    # server into ``backup_dir/dinary.db``. Same mechanism as
    # ``inv backup`` / ``_remote_snapshot_cmd``: ``sqlite3 .backup``
    # runs against a consistent view of the live DB even under WAL,
    # and the trap tears the snapshot down on the server on every
    # exit path so a failed deploy never leaks a large ``/tmp``
    # file.
    #
    # The ``test -f`` guard is the first-deploy safety net: on a
    # pristine server the source DB does not exist yet, and running
    # ``sqlite3 "$missing" ".backup ..."`` would silently open the
    # path read-write, create a zero-row DB there, and produce a
    # valid-but-empty backup. The guard prints ``__SKIP_NO_DB__``
    # on stderr and exits 0 so the Python side can detect "no DB
    # yet" vs. "real failure"; either way we clean up the local
    # placeholder so the operator never sees an empty
    # ``backups/pre-deploy-.../dinary.db`` they might mistake for
    # a valid snapshot.
    remote_cmd = (
        # The ``test -f`` guard runs BEFORE the shared snapshot
        # prologue: ``set -e`` from the prologue would otherwise
        # abort the whole script on a missing source DB, losing the
        # chance to emit the ``__SKIP_NO_DB__`` marker the Python
        # side needs to distinguish "no DB yet" from "real failure".
        "set -e; "
        f'if [ ! -f "{_REMOTE_DB_PATH}" ]; then '
        "  echo __SKIP_NO_DB__ 1>&2; exit 0; "
        "fi; "
        + _sqlite_backup_to_tmp_snapshot_prologue("dinary-pre-deploy-backup")
        + 'cat "$SNAP"'
    )
    b64 = base64.b64encode(remote_cmd.encode()).decode()
    local_db = backup_dir / "dinary.db"
    need_cleanup = False
    with local_db.open("wb") as fh:
        try:
            subprocess.run(
                ["ssh", host, f"echo {b64} | base64 -d | bash"],
                stdout=fh,
                check=True,
            )
        except subprocess.CalledProcessError:
            # Real failure (SFTP disconnect mid-stream, sqlite3
            # error, ssh transport issue). The file may be
            # partially written; remove it so the operator never
            # sees a corrupted placeholder. The deploy itself is
            # allowed to continue — this is a best-effort safety
            # net, not a gate.
            print(
                "=== Pre-deploy backup failed; continuing with deploy. ===",
            )
            need_cleanup = True
    # Zero-byte output means the remote guard printed
    # ``__SKIP_NO_DB__`` and exited 0 — treat that as "no DB yet"
    # and clean up the empty file.
    if not need_cleanup and local_db.exists() and local_db.stat().st_size == 0:
        print(
            f"=== Pre-deploy backup skipped (no {_REMOTE_DB_PATH} yet); "
            "continuing with deploy. ===",
        )
        need_cleanup = True
    if need_cleanup:
        local_db.unlink(missing_ok=True)

    print("=== Deploying dinary ===")
    if ref:
        _ssh(
            c,
            f"cd ~/dinary && git fetch --tags && git checkout {ref} && source ~/.local/bin/env && uv sync --no-dev",
        )
        print(
            f"=== WARNING: Remote is in detached HEAD at '{ref}'. "
            "Future `inv deploy` without --ref will `git pull` on whatever branch is checked out. ==="
        )
    else:
        _ssh(c, "cd ~/dinary && git pull && source ~/.local/bin/env && uv sync --no-dev")

    print("=== Ensuring data/ directory ===")
    _ssh(c, "mkdir -p ~/dinary/data")

    print("=== Syncing .deploy/.env to server ===")
    _sync_remote_env(c)

    print("=== Syncing .deploy/import_sources.json to server (if present) ===")
    _sync_remote_import_sources(c)

    bind_host = _bind_host(tunnel)
    print(f"=== Re-rendering dinary systemd unit (bind {bind_host}) ===")
    _render_service(c, "dinary", DINARY_SERVICE.format(host=bind_host))

    print("=== Cleaning up legacy ~/dinary/.env (if present) ===")
    _ssh(c, f"rm -f {REMOTE_LEGACY_ENV_PATH}")

    if not no_restart:
        print("=== Applying schema migrations ===")
        _ssh(
            c,
            "cd ~/dinary && source ~/.local/bin/env && "
            "uv run python -c 'from dinary.services import ledger_repo; ledger_repo.init_db(); "
            'print("Migrated data/dinary.db")\'',
        )
        print("=== Bootstrapping runtime catalog (idempotent) ===")
        bootstrap_catalog(c)

    print("=== Building _static/ with version ===")
    _ssh(c, "cd ~/dinary && source ~/.local/bin/env && uv run inv build-static")

    if no_restart:
        print(
            "=== --no-restart set: SKIPPING systemctl restart and schema migration. ===\n"
            "=== Next steps in the coordinated reset flow:   ===\n"
            "===   inv stop                                  ===\n"
            "===   ssh $HOST 'rm -f ~/dinary/data/dinary.db*'  ===\n"
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
        "cd ~/dinary && source ~/.local/bin/env && uv run python -c '"
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
        "cd ~/dinary && source ~/.local/bin/env && uv run python -c '"
        "from dinary.imports.seed import seed_from_sheet; "
        "import json; print(json.dumps(seed_from_sheet()))'",
    )


@task(name="migrate")
def migrate(c):
    """Apply pending migrations to ``data/dinary.db`` on the server."""
    _ssh(
        c,
        "cd ~/dinary && source ~/.local/bin/env && "
        "uv run python -c 'from dinary.services import ledger_repo; "
        'ledger_repo.init_db(); print("Migrated data/dinary.db")\'',
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

    Never deletes ``data/dinary.db``. Ledger tables
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
        "cd ~/dinary && source ~/.local/bin/env && uv run python -c '"
        "from dinary.imports.seed import rebuild_config_from_sheets; "
        "import json; print(json.dumps(rebuild_config_from_sheets()))'",
    )


@task(name="import-budget")
def import_budget(c, year="", yes=False):
    """DESTRUCTIVE: Delete every expense for the given year and re-import from Sheet.

    Operates on the single ``data/dinary.db`` file: the year's rows in
    ``expenses``/``expense_tags``/``sheet_logging_jobs`` are removed and
    re-imported from Google Sheets. Other years stay untouched.
    """
    year_int = _require_year(year)
    if not _require_yes(
        yes,
        f"WARNING: import-budget will DELETE every expense for {year_int} in "
        "data/dinary.db and re-import from the Google Sheet. Other years "
        "are untouched. There is no manual-vs-import distinction anymore; "
        "nothing in the targeted year is preserved.",
    ):
        return
    _ssh(
        c,
        "cd ~/dinary && source ~/.local/bin/env && uv run python -c '"
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
        "cd ~/dinary && source ~/.local/bin/env && uv run python -c '"
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
        "cd ~/dinary && source ~/.local/bin/env && uv run python -c '"
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
        "cd ~/dinary && source ~/.local/bin/env && uv run python -c '"
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
        "cd ~/dinary && source ~/.local/bin/env && uv run python -c '"
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
        "cd ~/dinary && source ~/.local/bin/env && uv run python -c '"
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
        "cd ~/dinary && source ~/.local/bin/env && uv run python -c '"
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
        "cd ~/dinary && source ~/.local/bin/env && uv run python -c '"
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
    """Take a consistent SQLite snapshot on the server and download it.

    Under SQLite WAL a raw ``scp data/dinary.db`` from a live server
    would copy a stale main file and miss whatever committed pages
    only live in the WAL yet — the resulting download would look
    valid on open (SQLite replays the WAL, if present) but silently
    lose the tail of the write history.

    Instead we invoke ``sqlite3 "$DB" ".backup $SNAP"`` on the server,
    which uses SQLite's online-backup API and captures a
    transactionally consistent snapshot while the service keeps
    writing. The snapshot is written into ``/tmp`` on the server,
    ``scp``'d home, and torn down via ``trap`` on exit so a failure
    never leaks a multi-hundred-MB file into ``/tmp``.

    Output lands in ``~/Library/dinary/<ts>/dinary.db`` — matching
    the default layout so operators can copy the file straight into
    ``data/`` and point ``inv dev`` at it.
    """
    dest = Path.home() / "Library" / "dinary"
    ts = _dt.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = dest / ts
    backup_dir.mkdir(parents=True, exist_ok=True)
    host = _host()
    # See ``_sqlite_backup_to_tmp_snapshot_prologue`` for why this
    # goes through ``sqlite3 .backup`` instead of ``scp``.
    remote_cmd = (
        _sqlite_backup_to_tmp_snapshot_prologue("dinary-backup") + 'cat "$SNAP"'
    )
    # Use a base64 envelope as with ``_ssh_capture_bytes`` but stream
    # the bytes of the snapshot straight into a local file — the
    # backup file is potentially large and we don't want to buffer
    # it in memory.
    b64 = base64.b64encode(remote_cmd.encode()).decode()
    local_db = backup_dir / "dinary.db"
    with local_db.open("wb") as fh:
        subprocess.run(
            ["ssh", host, f"echo {b64} | base64 -d | bash"],
            stdout=fh,
            check=True,
        )
    print(f"Backed up to {local_db}")


@task(name="verify-db")
def verify_db(c, remote=False):
    """Run SQLite's ``PRAGMA integrity_check`` + ``PRAGMA foreign_key_check``.

    Both pragmas are read-only and cheap for a DB on the order of a
    few hundred MB. ``integrity_check`` walks every btree page and
    reports structural damage (torn pages, index/table mismatches,
    orphan freelist entries); ``foreign_key_check`` lists every row
    that violates a declared FK. A healthy DB prints ``ok`` for the
    first and zero rows for the second. This is the
    ``.plans/storage-migration.md`` verification gate for the post-
    migration rollout: a silent corruption that slipped past
    ``inv verify-bootstrap-import-all`` would still be caught here.

    Flags:
        --remote   run against a ``/tmp`` snapshot of the prod DB
                   over SSH (same snapshot wrapper as
                   ``inv report-*``). Default runs locally against
                   ``data/dinary.db``.

    Exits non-zero when ``integrity_check`` prints anything other
    than ``ok`` or when ``foreign_key_check`` reports at least one
    offending row, so CI / coordinated-reset flows can fail loud.
    """
    if remote:
        # See ``_sqlite_backup_to_tmp_snapshot_prologue`` for why we
        # snapshot first instead of pragma-ing the live file.
        remote_cmd = (
            _sqlite_backup_to_tmp_snapshot_prologue("dinary-verify-db")
            + 'sqlite3 "$SNAP" "PRAGMA integrity_check; PRAGMA foreign_key_check;"'
        )
        raw = _ssh_capture_bytes(remote_cmd)
        output = raw.decode("utf-8", errors="replace")
    else:
        db_path = Path("data/dinary.db")
        if not db_path.exists():
            print(f"No local DB at {db_path}; run `inv dev` or `inv backup` first.", file=sys.stderr)
            sys.exit(1)
        # Run both pragmas via the stdlib bindings so the local path
        # has no dependency on a system ``sqlite3`` CLI (Windows
        # runners don't ship one). Output lines are formatted the
        # same as the CLI's default ``list`` mode (``|``-joined
        # columns) so the shared ``lines == ["ok"]`` gate below
        # treats stdlib- and CLI-produced output identically, and the
        # ``--remote`` branch — which keeps using the server's CLI
        # against a ``/tmp`` snapshot — stays byte-compatible with
        # the local path.
        con = sqlite3.connect(db_path)
        try:
            rows = con.execute("PRAGMA integrity_check").fetchall()
            rows.extend(con.execute("PRAGMA foreign_key_check").fetchall())
        finally:
            con.close()
        output = "\n".join("|".join(str(col) for col in row) for row in rows)
    print(output, end="" if output.endswith("\n") else "\n")
    # ``integrity_check`` prints a single ``ok`` line when the DB is
    # healthy and one line per problem otherwise.
    # ``foreign_key_check`` prints nothing when every FK resolves and
    # ``table|rowid|parent|fkid`` rows otherwise. Either deviation is
    # a hard failure.
    lines = [line for line in output.splitlines() if line.strip()]
    if lines != ["ok"]:
        print("=== verify-db FAILED ===", file=sys.stderr)
        sys.exit(1)
    print("=== verify-db OK ===")


@task(name="litestream-setup")
def litestream_setup(c):
    """Install Litestream on VM 1 and start the replicator sidecar.

    One-time bootstrap for the Phase 2 hot replica (see
    ``.plans/storage-migration.md``). This is a passive sidecar: it
    reads WAL segments from ``data/dinary.db`` and ships them to the
    SFTP replica declared in ``.deploy/litestream.yml``. The app
    service never talks to Litestream — if the sidecar is down the
    app still writes fine, it just stops being replicated until
    ``systemctl start litestream`` is called.

    Preconditions on the operator's side:
        1. ``.deploy/litestream.yml`` exists locally, copied from
           ``.deploy.example/litestream.yml`` and edited with the
           SFTP target (``host``, ``user``, ``path``, ``key-path``).
        2. The ``id_ed25519.pub`` on VM 1 is in the replica host's
           ``~/.ssh/authorized_keys`` (run ``ssh-copy-id`` manually
           once; we do not automate cross-host trust here because
           the replica host is operator-picked and out of scope).

    The task is idempotent: re-running it upgrades Litestream, re-
    uploads the config, and restarts the sidecar. It does NOT try
    to bootstrap the replica target — first writes happen naturally
    once the sidecar is up and the app issues its next commit.
    """
    if not Path(LOCAL_LITESTREAM_CONFIG_PATH).exists():
        print(
            f"No {LOCAL_LITESTREAM_CONFIG_PATH} locally.\n"
            f"Copy {LOCAL_LITESTREAM_EXAMPLE_PATH} to {LOCAL_LITESTREAM_CONFIG_PATH} "
            "and fill in the SFTP target, then retry.",
            file=sys.stderr,
        )
        sys.exit(1)

    print("=== Installing Litestream binary ===")
    # Litestream ships prebuilt ``.deb`` files per release; pinning
    # explicitly rather than using the ``latest`` shortcut makes
    # ``inv litestream-setup`` reproducible across VMs (two operators
    # bootstrapping VMs a week apart end up on the same binary).
    #
    # Architecture detection is non-trivial because Oracle Free Tier
    # offers both x86_64 Micro and Ampere (arm64) shapes, and the
    # release assets use ``x86_64`` / ``arm64`` as filename suffixes
    # (NOT the dpkg ``amd64`` / ``arm64`` names — mismatched on
    # x86_64). ``uname -m`` returns the upstream-friendly name on both.
    _ssh(c, _litestream_install_script())

    print(f"=== Uploading {LOCAL_LITESTREAM_CONFIG_PATH} to {REMOTE_LITESTREAM_CONFIG_PATH} ===")
    content = Path(LOCAL_LITESTREAM_CONFIG_PATH).read_text(encoding="utf-8")
    _write_remote_file(c, REMOTE_LITESTREAM_CONFIG_PATH, content)
    _ssh_sudo(c, f"chown root:root {REMOTE_LITESTREAM_CONFIG_PATH} "
                 f"&& chmod 644 {REMOTE_LITESTREAM_CONFIG_PATH}")

    print("=== Creating litestream systemd service ===")
    _create_service(c, "litestream", LITESTREAM_SERVICE)

    print("=== Checking status ===")
    _ssh(c, "sleep 3 && systemctl is-active litestream && "
            "sudo journalctl -u litestream -n 20 --no-pager")


@task(name="litestream-status")
def litestream_status(c):
    """Show the remote Litestream sidecar's health and replica state.

    Prints the systemd unit status, the most recent journal lines,
    and the replicator's own view of the managed DB
    (``litestream databases`` — the v0.5 replacement for the old
    ``snapshots`` inventory subcommand, which was removed when the
    storage layer was rewritten around LTX files). A healthy sidecar
    prints the DB path and at least one generation; a silent output
    means the sidecar either never ran or lost SFTP credentials.
    """
    _ssh(c, "systemctl status litestream --no-pager || true")
    _ssh(c, "sudo journalctl -u litestream -n 30 --no-pager || true")
    _ssh(c, f"litestream databases -config {REMOTE_LITESTREAM_CONFIG_PATH} || true")


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
                   runs locally against ``data/dinary.db``)

    ``--remote`` uses the same JSON-over-SSH transport as
    ``inv report-*`` (see :func:`_run_report_module`): the server
    always emits ``--json`` against a consistent SQLite snapshot of
    the live DB and the local process renders. That is the only way
    to keep Cyrillic / box-drawing glyphs intact across the SSH
    pipe.
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

    # Same snapshot wrapper the ``inv report-*`` tasks use so the
    # report module always runs against a transactionally-consistent
    # copy of the live DB, avoiding any races with in-flight writers.
    raw = _ssh_capture_bytes(
        _remote_snapshot_cmd("dinary.imports.report_2d_3d", _build_flags(force_json=True))
    )

    # ``--json --remote`` is the pipe-into-jq case: forward the
    # server's bytes verbatim so stdout is exactly what the remote
    # emitted.
    if json:
        sys.stdout.buffer.write(raw)
        return

    _render_2d3d_locally(raw, as_csv=csv)


#: Remote path of the live SQLite ledger file.
_REMOTE_DB_PATH = "/home/ubuntu/dinary/data/dinary.db"


def _sqlite_backup_to_tmp_snapshot_prologue(snap_prefix: str) -> str:
    """Return the common prologue that snapshots ``_REMOTE_DB_PATH`` to ``/tmp``.

    Four call sites (``inv deploy`` pre-backup, ``inv backup``,
    ``inv verify-db --remote``, and ``_remote_snapshot_cmd`` for the
    report tasks) all need the same shape: enable ``set -e``, stake
    out a per-invocation ``/tmp`` path in ``$SNAP``, register a
    ``trap`` so the snapshot is torn down on every exit path
    (success, failure, ``SIGINT``), and then invoke
    ``sqlite3 "$DB" ".backup \\"$SNAP\\""`` to materialize a
    transactionally consistent copy of the live DB.

    The trap is deliberately registered **before** the
    ``sqlite3 .backup`` so an interrupt between the two cannot leak
    the snapshot file. ``$$`` expands to the remote shell's PID so
    two parallel invocations from the same operator don't stomp on
    each other's snapshot path.

    ``snap_prefix`` lets each caller carry a self-identifying path
    (``dinary-backup``, ``dinary-verify-db``, ...) so a stale
    snapshot in ``/tmp`` after a crashed invocation is traceable to
    the task that created it.

    Returns a string that ends with ``"; "`` so callers can
    concatenate their epilogue (``cat "$SNAP"`` to stream bytes home,
    a second ``sqlite3 "$SNAP" "PRAGMA ..."`` to validate the
    snapshot, or a full ``DINARY_DATA_PATH=$SNAP python -m ...``
    report invocation) without worrying about separator bookkeeping.
    """
    return (
        "set -e; "
        f"SNAP=/tmp/{snap_prefix}-$$.db; "
        'trap "rm -f \\"$SNAP\\"" EXIT; '
        f'sqlite3 "{_REMOTE_DB_PATH}" ".backup \\"$SNAP\\""; '
    )


def _remote_snapshot_cmd(module_path: str, flags: list[str]) -> str:
    """Build a remote shell command that runs a read-only report module
    against a consistent SQLite snapshot of the live DB.

    SQLite in WAL mode would let a reader open the live file
    concurrently with the running writer, but doing so races with
    in-flight checkpoints and with Litestream's replication cadence
    — the reader could land on a page mid-rewrite and surface an
    ephemeral inconsistency. Instead we ``sqlite3 .backup`` the
    primary file into ``/tmp``, point ``DINARY_DATA_PATH`` at the
    snapshot, and run the read-only report module against that
    isolated copy. The snapshot is torn down on every exit path via
    ``trap`` so a failed report never leaks a multi-hundred-MB file.

    Snapshot consistency: SQLite's online-backup API copies the
    database page-by-page while holding just enough locks to keep
    the reader's view transactionally consistent, even while the
    service keeps writing. The snapshot reflects the last
    committed transaction at the moment ``.backup`` released.

    Unique per-invocation snapshot path: ``$$`` expands to the
    remote shell's PID so two parallel ``inv report-*`` runs from
    separate laptops cannot stomp on the same ``/tmp`` file.

    ``module_path`` is the full dotted path (``dinary.reports.income``,
    ``dinary.imports.report_2d_3d``, ...) so the same wrapper serves
    both ``inv report-*`` and ``inv import-report-2d-3d --remote``.
    """
    report = f"uv run python -m {module_path}"
    if flags:
        report = f"{report} {' '.join(flags)}"
    # Prologue (``set -e`` + trap + ``sqlite3 .backup``) is shared
    # with ``inv backup`` / ``inv verify-db --remote`` / ``inv deploy``
    # pre-backup — see ``_sqlite_backup_to_tmp_snapshot_prologue``.
    return (
        _sqlite_backup_to_tmp_snapshot_prologue("dinary-report-snapshot")
        + "cd ~/dinary && source ~/.local/bin/env && "
        + f'DINARY_DATA_PATH="$SNAP" {report}'
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
      a single process fetches from ``data/dinary.db`` and renders.
    * Remote: the same module runs on the server in ``--json`` mode
      via the SSH snapshot wrapper (see :func:`_remote_snapshot_cmd`
      for why a SQLite snapshot is used), the JSON bytes come back
      through :func:`_ssh_capture_bytes`, and the module's
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
    raw = _ssh_capture_bytes(_remote_snapshot_cmd(f"dinary.reports.{module}", remote_flags))

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
                           runs locally against ``data/dinary.db``
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
                   locally against ``data/dinary.db``.

    One row per calendar year, with per-year total, count of
    months-with-data, and average per data-month.
    """
    flags: list[str] = []
    if csv:
        flags.append("--csv")
    _run_report_module(c, "income", flags, remote=remote)


@task(
    name="sql",
    help={
        "query": "SQL query string (mutex with --file).",
        "file": "Read SQL from file at this path (mutex with --query).",
        "csv": "Emit CSV to stdout instead of a rich table.",
        "json": (
            "Emit JSON envelope {columns, rows, row_count} to stdout. "
            "Mutex with --csv."
        ),
        "write": (
            "Open the DB read-write so UPDATE/DELETE/INSERT can run. "
            "Off by default; forbidden with --remote."
        ),
        "remote": (
            "Run against a /tmp snapshot of the prod DB over SSH "
            "instead of local data/dinary.db."
        ),
    },
)
def sql_query(c, query="", file="", csv=False, json=False, write=False, remote=False):  # noqa: A002
    """Run a SQL query against ``data/dinary.db``.

    By default the connection is opened ``mode=ro`` via the SQLite
    URI form — typoing ``UPDATE`` / ``DELETE`` errors out at the
    SQLite layer instead of quietly mutating the ledger. Pass
    ``--write`` to explicitly opt into mutations for one-off fixups.
    For free-form inspection of the ``app_metadata`` anchor,
    per-currency totals in ``expenses``, sheet-logging job state,
    etc. Report-shaped queries should still live in
    ``dinary.reports.*``.

    Examples::

        inv sql -q "SELECT * FROM app_metadata ORDER BY key"
        inv sql -q "SELECT currency_original, COUNT(*) FROM expenses GROUP BY 1"
        inv sql -f scripts/monthly_summary.sql --csv > out.csv
        inv sql -q "SELECT * FROM app_metadata" --remote
        inv sql -q "DELETE FROM expenses WHERE id = 999" --write

    ``--write`` is rejected together with ``--remote``: mutating
    prod through an SSH pipe into a ``/tmp`` snapshot would silently
    discard the writes when the snapshot is torn down on exit, which
    is a far worse failure mode than a clear "not allowed" error.
    Use ``ssh`` + ``inv sql --write`` on the host for real prod fixups,
    or better — write a proper migration.

    Local concurrency: SQLite in WAL mode lets an ``inv sql --`` run
    read concurrently with a live ``inv dev`` uvicorn writer, so
    you don't need to stop the dev server first. ``--write`` locally
    still needs exclusive file access to the page it writes, so
    running ``--write`` concurrently with the dev server may either
    block briefly (busy_timeout) or surface a ``database is locked``
    error — stop the dev server first for anything non-trivial.

    ``--remote`` follows the same JSON-over-SSH snapshot pattern as
    ``inv report-*`` (see :func:`_remote_snapshot_cmd`): a
    transactionally consistent SQLite snapshot is taken on the
    server via ``sqlite3 .backup``, the module emits a JSON
    envelope, and the local process either forwards those bytes
    (``--csv`` / ``--json``) or renders a rich table.

    ``--file`` + ``--remote`` reads the SQL file locally and ships
    its contents as ``--query`` over SSH — no SCP round-trip.
    """
    if csv and json:
        raise SystemExit("--csv and --json are mutually exclusive")
    if bool(query) == bool(file):
        raise SystemExit("exactly one of --query / --file is required")
    if write and remote:
        raise SystemExit(
            "--write is not allowed with --remote: the remote runs against a "
            "/tmp snapshot that is torn down on exit, so any mutations would "
            "be silently discarded. SSH to the host and run `inv sql --write` "
            "there, or write a proper migration.",
        )

    if file:
        # Even for ``--remote`` we dereference the file locally and
        # ship its contents as ``--query``; that keeps the SSH
        # transport path uniform and sidesteps having to SCP a
        # throwaway ``.sql`` before each run.
        sql_text = Path(file).read_text(encoding="utf-8")
    else:
        sql_text = query

    # ``shlex.quote`` is the pivot that makes arbitrary SQL survive
    # the space-join in ``_remote_snapshot_cmd`` → ``bash`` decode.
    # Without it, a query containing a space or quote would be
    # re-split by the remote shell and argparse would see garbage.
    sql_flags = ["--query", shlex.quote(sql_text)]

    if not remote:
        local_flags = [*sql_flags]
        if csv:
            local_flags.append("--csv")
        elif json:
            local_flags.append("--json")
        if write:
            local_flags.append("--write")
        c.run(f"uv run python -m dinary.tools.sql {' '.join(local_flags)}")
        return

    # Remote path: the server always emits the JSON envelope. When
    # the operator asked for ``--csv`` / ``--json`` we just forward
    # bytes (for ``--json``) or re-render client-side; for the
    # default rich path we render locally against the JSON so ANSI
    # colours aren't stripped by the SSH transport.
    remote_flags = [*sql_flags, "--json"]
    raw = _ssh_capture_bytes(_remote_snapshot_cmd("dinary.tools.sql", remote_flags))

    if json:
        sys.stdout.buffer.write(raw)
        return

    payload = _json.loads(raw.decode("utf-8"))
    columns, rows = sql_module.rows_from_json(payload)
    if csv:
        sql_module.render_csv(columns, rows, stream=sys.stdout)
    else:
        sql_module.render_rich(columns, rows, stream=sys.stdout)


namespace = Collection.from_module(sys.modules[__name__])
for name in ALLOWED_VERSION_TYPES:
    namespace.add_task(ver_task_factory(name), name=f"ver-{name}")  # type: ignore[bad-argument-type]
for name in ALLOWED_DOC_LANGUAGES:
    namespace.add_task(docs_task_factory(name), name=f"docs-{name}")  # type: ignore[bad-argument-type]
