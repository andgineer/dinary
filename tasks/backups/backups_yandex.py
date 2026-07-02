"""Yandex.Disk rclone bootstrap, shared by ``inv setup-replica`` (VM2, via SSH) and
``inv setup-yadisk`` (local machine)."""

import base64
import getpass
import subprocess
import sys

from invoke import task

from tasks.devtools.env import replica_host


def replica_has_working_yandex_remote():
    """Exact-line match on ``yandex:`` (not substring) plus an ``rclone lsd`` smoke test.
    Deletes a broken remote inline so the caller re-prompts instead of silently reusing it.
    """
    probe = (
        "set -eu\n"
        "if ! rclone listremotes 2>/dev/null | grep -qx 'yandex:'; then\n"
        "  exit 1\n"
        "fi\n"
        "if ! rclone lsd yandex: >/dev/null 2>&1; then\n"
        "  rclone config delete yandex >/dev/null 2>&1 || true\n"
        "  echo 'stale yandex: remote removed on VM2 — re-prompting' >&2\n"
        "  exit 1\n"
        "fi\n"
    )
    result = subprocess.run(
        ["ssh", replica_host(), probe],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.stderr:
        sys.stderr.write(result.stderr)
    return result.returncode == 0


def prompt_yandex_credentials():
    """Split out so tests can mock the IO layer. Exits on empty input rather than
    risk a half-created rclone remote."""
    print(
        "\n"
        "=== Yandex.Disk WebDAV credentials ===\n"
        "\n"
        "IMPORTANT: Yandex WebDAV does NOT accept your regular Yandex\n"
        "account password. You need an APP-PASSWORD — a separate,\n"
        "scoped password Yandex generates for one specific app.\n"
        "\n"
        "How to get one:\n"
        "  1. Open https://id.yandex.ru/security/app-passwords\n"
        '  2. Click "Create a new password".\n'
        '  3. Pick the "Files" (Файлы / WebDAV) category — NOT Mail,\n'
        "     Calendar, or generic.\n"
        "  4. Yandex shows a 16-character password ONCE (often rendered\n"
        "     as 4 blocks of 4 letters). Copy it immediately; it\n"
        "     cannot be retrieved later, only regenerated.\n"
        "  5. Paste it below. You can revoke it from the same page at\n"
        "     any time without affecting your main Yandex password.\n"
        "\n"
        "Plaintext never touches disk. The obscured form is written\n"
        "to ~ubuntu/.config/rclone/rclone.conf on VM2.\n",
    )
    login = input("Yandex login (without @yandex.ru): ").strip()
    if not login:
        sys.stderr.write("Empty login; aborting.\n")
        sys.exit(1)
    app_password = getpass.getpass(
        "Yandex APP-PASSWORD (generate at https://id.yandex.ru/security/app-passwords → Files): ",
    )
    if not app_password:
        sys.stderr.write("Empty app-password; aborting.\n")
        sys.exit(1)
    return login, app_password


def install_yandex_rclone_remote(login: str, app_password: str) -> None:
    """Credentials travel via ssh stdin, never argv/shell history. Uses ``key value``
    pairs, not ``key=value`` (rclone silently drops fields with the latter), and
    ``--no-obscure`` (the password is pre-obscured; re-obscuring would corrupt it).
    Rolls back the remote on a failed smoke test so the next run re-prompts."""
    inner = (
        "set -euo pipefail\n"
        "read -r LOGIN\n"
        "read -rs PASS\n"
        'OBS=$(printf "%s" "$PASS" | rclone obscure -)\n'
        "rclone config create --no-obscure yandex webdav "
        "url https://webdav.yandex.ru "
        "vendor other "
        'user "$LOGIN" '
        'pass "$OBS" >/dev/null\n'
        "echo '--- written [yandex] config (pass redacted) ---' >&2\n"
        "sed -n '/^\\[yandex\\]/,/^\\[/p' "
        '"$HOME/.config/rclone/rclone.conf" '
        "| sed 's/^pass = .*/pass = <redacted>/' >&2\n"
        "echo '--- smoke test: rclone lsd yandex: ---' >&2\n"
        "if ! rclone lsd yandex: --low-level-retries 1 --retries 1 -v; then\n"
        "  rclone config delete yandex >/dev/null 2>&1 || true\n"
        "  echo '' >&2\n"
        "  echo 'rclone lsd yandex: failed; broken remote removed from VM2' >&2\n"
        "  exit 1\n"
        "fi\n"
    )
    inner_b64 = base64.b64encode(inner.encode()).decode()
    outer = (
        "set -euo pipefail; "
        "T=$(mktemp); "
        'trap "rm -f \\"$T\\"" EXIT; '
        f'echo {inner_b64} | base64 -d > "$T"; '
        'bash "$T"'
    )
    proc = subprocess.run(
        ["ssh", replica_host(), outer],
        input=f"{login}\n{app_password}\n",
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        sys.stderr.write(
            "\nrclone config failed on the replica. Common causes:\n"
            "  * a regular Yandex account password was used instead of\n"
            "    an APP-PASSWORD. Yandex WebDAV accepts only app-\n"
            "    passwords from https://id.yandex.ru/security/app-passwords\n"
            "    (even on accounts without 2FA).\n"
            "  * the app-password was created for a category other than\n"
            '    "Files" (Файлы / WebDAV) — e.g. "Mail" tokens are\n'
            "    scoped differently and the WebDAV endpoint rejects them.\n"
            '  * the login was a display name or email (e.g. "Joe Doe",\n'
            '    "joe@yandex.ru") instead of the bare Yandex login.\n'
            "  * the app-password was mistyped (it is usually shown as\n"
            "    4 blocks of 4 letters; paste WITHOUT spaces).\n"
            "  * the replica cannot reach https://webdav.yandex.ru.\n"
            "\n"
            "The partially-created 'yandex:' remote has been rolled\n"
            "back on VM2.\n"
            "Re-run `inv setup-replica` to try again; it will\n"
            "prompt for credentials fresh.\n",
        )
        sys.exit(proc.returncode)


def local_has_working_yandex_remote() -> bool:
    """Local equivalent of :func:`replica_has_working_yandex_remote` (no SSH)."""
    listed = subprocess.run(
        ["rclone", "listremotes"],
        capture_output=True,
        text=True,
        check=False,
    )
    if "yandex:" not in listed.stdout.splitlines():
        return False
    lsd = subprocess.run(
        ["rclone", "lsd", "yandex:", "--low-level-retries", "1", "--retries", "1"],
        capture_output=True,
        text=True,
        check=False,
    )
    if lsd.returncode != 0:
        subprocess.run(
            ["rclone", "config", "delete", "yandex"],
            capture_output=True,
            check=False,
        )
        sys.stderr.write("stale yandex: remote removed from local rclone — re-prompting\n")
        return False
    return True


def install_local_yandex_rclone_remote(login: str, app_password: str) -> None:
    """Local equivalent of :func:`install_yandex_rclone_remote` (no SSH); same
    ``key value`` and ``--no-obscure`` requirements apply."""
    obs = subprocess.run(
        ["rclone", "obscure", "-"],
        input=app_password,
        capture_output=True,
        text=True,
        check=True,
    )
    obscured = obs.stdout.strip()
    subprocess.run(
        [
            "rclone",
            "config",
            "create",
            "--no-obscure",
            "yandex",
            "webdav",
            "url",
            "https://webdav.yandex.ru",
            "vendor",
            "other",
            "user",
            login,
            "pass",
            obscured,
        ],
        check=True,
        capture_output=True,
    )
    lsd = subprocess.run(
        ["rclone", "lsd", "yandex:", "--low-level-retries", "1", "--retries", "1", "-v"],
        check=False,
    )
    if lsd.returncode != 0:
        subprocess.run(
            ["rclone", "config", "delete", "yandex"],
            capture_output=True,
            check=False,
        )
        sys.stderr.write("rclone lsd yandex: failed; broken remote removed\n")
        sys.exit(1)


def ensure_local_yandex_rclone_configured() -> None:
    """Called by ``inv setup-yadisk``, and by ``inv restore-yadisk`` when the
    remote is absent."""
    if local_has_working_yandex_remote():
        print("yandex: remote already configured and healthy — skipping prompt.")
        return
    login, app_password = prompt_yandex_credentials()
    install_local_yandex_rclone_remote(login, app_password)
    print("yandex: remote configured and verified (rclone lsd succeeded).")


@task(name="setup-yadisk")
def setup_yadisk(c):  # noqa: ARG001
    """Configure the yandex: WebDAV rclone remote on this machine. Idempotent."""
    ensure_local_yandex_rclone_configured()


def ensure_yandex_rclone_configured(c):  # noqa: ARG001
    """No-op if the remote already works. Uses WebDAV + app-password instead of
    rclone's OAuth wizard because VM2 is headless — the laptop-authorize/copy-token
    dance across machines is an error magnet during disaster recovery."""
    if replica_has_working_yandex_remote():
        print("yandex: remote already configured and healthy — skipping prompt.")
        return
    login, app_password = prompt_yandex_credentials()
    install_yandex_rclone_remote(login, app_password)
    print("yandex: remote configured and verified (rclone lsd succeeded).")
