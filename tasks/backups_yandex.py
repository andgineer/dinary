"""Yandex.Disk rclone bootstrap on VM2.

Splits out the interactive credential prompt + ``rclone config create``
flow so :func:`ensure_yandex_rclone_configured` can be invoked from
``inv setup-replica`` (or any future task) without dragging the rest of
the backup pipeline along.
"""

import base64
import getpass
import subprocess
import sys

from .env import replica_host


def replica_has_working_yandex_remote():
    """Return True iff VM2's rclone has a ``yandex:`` remote that
    actually works (auth + URL + scope + network all green).

    The contract here is "do we need to prompt the operator?", not
    "does a config section exist?". A half-written config (missing
    ``url``, wrong app-password, revoked scope) still needs the
    interactive bootstrap — otherwise the retention timer would
    quietly fail every night against a broken remote. So the probe:

    1. ``rclone listremotes`` must include exact line ``yandex:``
       — substring match against e.g. ``yandex-old:`` would falsely
       pass.
    2. ``rclone lsd yandex:`` must succeed. This smoke-tests the
       entire stack: URL scheme, credentials, scope, Yandex
       reachability.

    If the remote exists but fails the smoke-test, we delete it
    inline before returning False. That way the next
    :func:`ensure_yandex_rclone_configured` call prompts fresh
    credentials instead of silently keeping a broken config around
    across runs (the exact bug that bit us when
    ``rclone config create`` silently dropped ``key=value`` fields).
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
    """Interactive prompt for Yandex login + app-password.

    Split out so the IO layer is mockable — the in-flow tests stub
    this to feed deterministic credentials without poking a real
    terminal. Returns ``(login, app_password)``; raises SystemExit
    on empty input to keep the caller out of the partial-config
    weeds (a half-created rclone remote is worse than no remote).
    """
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
    """Create the ``yandex:`` WebDAV remote on VM2 without shell leakage.

    Security shape:

    * Login AND app-password travel as two lines on the ssh stdin,
      consumed by ``read -r`` / ``read -rs``. Neither lands in argv
      (``ps`` listing), shell history, or the script image shipped
      to VM2. The plaintext password additionally dies inside
      ``rclone obscure -`` the moment it is converted to its
      obscured on-disk form.
    * ``rclone config create`` writes the obscured form to
      ``~ubuntu/.config/rclone/rclone.conf``; that is the same format
      the ``rclone config`` interactive wizard produces, so subsequent
      manual edits remain possible.

    Two sharp invariants past versions of this helper got wrong:

    * ``rclone config create`` wants ``key value`` pairs separated by
      spaces, NOT ``key=value``. The latter form silently drops
      fields on current rclone releases: an earlier version of this
      helper produced a ``[yandex]`` section with no ``url`` line,
      which then failed every operation with
      ``unsupported protocol scheme ""`` — and stuck around across
      re-runs because ``rclone listremotes`` still reported
      ``yandex:``.
    * ``rclone config create`` re-obscures ``pass`` by default, so
      feeding the already-obscured value without ``--no-obscure``
      corrupts it. We pass ``--no-obscure`` explicitly.

    Smoke-tests end-to-end with ``rclone lsd yandex:``. If the smoke
    test fails (wrong app-password, wrong scope, typo, Yandex
    unreachable) we **roll back** by deleting the just-written
    ``yandex`` remote, so the next ``inv setup-replica``
    re-prompts for fresh credentials instead of silently reusing the
    broken config.
    """
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


def ensure_yandex_rclone_configured(c):  # noqa: ARG001
    """Interactive bootstrap for the ``yandex:`` rclone remote on VM2.

    No-op when the remote already exists — the typical flow on
    re-runs of :func:`setup_replica`. The first-time path
    prompts the operator for their Yandex login + app-password (app-
    password URL printed in the prompt text) and writes the remote
    to VM2 non-interactively via ``rclone config create``.

    The interactive OAuth flow that the rclone wizard would trigger
    is avoided on purpose: VM2 is headless, and the
    laptop-authorize/copy-token dance across machines is an error
    magnet during a future disaster recovery. WebDAV + an app-
    password is equivalent for our access pattern (PUT/DELETE of
    uploaded files) and the app-password can be revoked from
    Yandex's account UI at any time.
    """
    if replica_has_working_yandex_remote():
        print("yandex: remote already configured and healthy — skipping prompt.")
        return
    login, app_password = prompt_yandex_credentials()
    install_yandex_rclone_remote(login, app_password)
    print("yandex: remote configured and verified (rclone lsd succeeded).")
