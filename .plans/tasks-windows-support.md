# Windows support for `inv` operator tasks

## Context

The `tasks/` package ships a single laptop-side workflow: the operator
runs `inv <task>` to deploy, back up, restore, and query the live VM1
DB. Today macOS and Linux are first-class; Windows is incidental. The
recent regression test for commit `527623d62` (`remote_snapshot_cmd`
flag quoting) had to be unconditionally `skipif win32` because the
*generated* shell snippet is bash-only — and that triggered a question:
**which other tasks would silently fall over for a Windows operator?**

This document captures the inventory and stages a path from
"Windows mostly works" to "every operator task runs cleanly on
Windows", ordered from cheap-and-obvious to "would require rewriting
half the deploy pipeline". No code changes belong to this plan
itself — it is the design we will pull work items out of.

## Goal

A Windows 10/11 operator using the bundled OpenSSH client and `uv`
should be able to run the everyday `inv` commands without invoking
Git Bash / WSL, with parity for: development (`dev`, `test`, `pre`,
`migrate`), monitoring (`status`, `logs`, `healthcheck`,
`backup-cloud-status`), data access (`sql`, `report-*`,
`import-report-2d-3d`), and emergency operator paths (`backup`,
`backup-cloud-restore`, `replica-resync`).

## Scope

**In scope.** Anything that runs on the operator's machine via
`c.run(...)` or `subprocess.run([...])` from inside `tasks/`.

**Out of scope.** Server-side bash scripts produced by builders in
`tasks/ssh_utils.py` and `tasks/backups.py`. They run on Ubuntu VM1 /
VM2 over SSH; the operator OS only ships the bytes. The
`skipif win32` decision in
`tests/tasks/test_tasks_ssh_utils.py:93` and
`tests/tasks/test_tasks_backups_restore.py:122` stays.

**Out of scope.** Building a CI matrix that runs the full deploy on
Windows. The regression we want to catch is "operator command crashes
or silently mis-quotes" — unit-level coverage is enough; we are not
provisioning real Windows VMs to test against.

## Current state — what runs where

Findings below are from `tasks/*.py` as of 2026-04-27. Tags:

- **OK** — works on Windows as-is.
- **WARN** — works but ugly (printed garbage, `pty=True` no-op,
  silent quirks).
- **BROKEN-CMD** — fails under `cmd.exe` (the default `invoke`
  shell on Windows); may work in Git Bash but we cannot assume that.
- **BROKEN-WIN** — fails on Windows regardless of shell (binary
  missing, OS-only API).

### Server-side bash → harmless on Windows (OK)

These send a string over `ssh`; bash runs on the remote.

- `tasks/ssh_utils.py:43, 49, 102, 108` — `ssh_run`, `ssh_replica`,
  `write_remote_file`, `write_remote_replica_file`.
- `tasks/db.py:111` — `inv backup` (argv-form `subprocess.run`).
- `tasks/deploy.py:91` — `inv deploy` pre-deploy snapshot.
- `tasks/backups.py:90, 212, 330, 343, 354` — Yandex/restore probes.
- All `_build_*` builders in `tasks/ssh_utils.py` / `tasks/backups.py`.

`subprocess.run(["ssh", host, payload], ...)` is argv-form, so cmd.exe
quoting never enters the picture. OpenSSH has shipped with Windows 10
since 1803. **No work needed.**

### Operator-side bash, broken on Windows

Concrete sites by severity:

| # | File:line | Symbol | Problem | Tag |
|---|-----------|--------|---------|-----|
| 1 | `tasks/dev.py:134` | `inv dev --reset` | `subprocess.run(["pkill", ...])` — `pkill` not on Windows; raises `FileNotFoundError` even with `check=False` | BROKEN-WIN |
| 2 | `tasks/dev.py:147` | `inv dev --reset` | `c.run("uv run python -c 'from dinary...'")` — single-quoted `-c` payload | BROKEN-CMD |
| 3 | `tasks/db.py:24` | `inv migrate` | Same single-quoted `-c` | BROKEN-CMD |
| 4 | `tasks/dev.py:66` | `inv test` | `c.run("rm -rf allure-results")` | BROKEN-CMD |
| 5 | `tasks/dev.py:55` | `inv uv` | `c.run("curl -LsSf ... \| sh")` | BROKEN-CMD |
| 6 | `tasks/dev.py:27` | `inv ver` | `c.run("./scripts/verup.sh ...")` | BROKEN-CMD |
| 7 | `tasks/dev.py:44-46` | `inv docs <lang>` | `open -a 'Google Chrome' ...`, `scripts/build-docs.sh` | BROKEN-WIN |
| 8 | `tasks/server.py:46` | `inv logs --remote` | `f"ssh {host()} 'sudo journalctl -u dinary {flag}'"` — single-quoted ssh payload | BROKEN-CMD |
| 9 | `tasks/server.py:74, 80` | `inv ssh`, `inv ssh-replica` | `c.run(f"ssh {host()}", pty=True)` | WARN |
| 10 | `tasks/dev.py:165` | `inv dev` | `c.run(cmd, pty=True)` | WARN |
| 11 | `tasks/setup.py:87` | `inv setup-server` | `scp ~/.config/gspread/...` — `~` not expanded by cmd.exe | BROKEN-CMD |
| 12 | `tasks/server.py:58` | `inv status` | `curl ... \|\| echo 'Server not responding'` — `\|\|` is fine, single quotes print literally | WARN |
| 13 | `tasks/reports.py:41` | `inv report-* ` (local) | `cmd = f"{cmd} {' '.join(flags)}"` — same family as `527623d62`; safe today only because all flags are alphanumeric | LATENT |
| 14 | `tasks/imports.py:360` | `inv import-report-2d-3d` (local) | Same `' '.join(flags)` shape | LATENT |
| 15 | `tasks/reports.py:210` | `inv sql` (local) | `c.run(f"... {shlex.join(local_flags)}")` — POSIX single-quote quoting under cmd.exe; multi-word `--query` will be tokenised wrong | BROKEN-CMD |
| 16 | `tasks/backups.py:507-508` | `inv backup-cloud-restore` | `shlex.quote(...)` injected into `c.run` strings | BROKEN-CMD + BROKEN-WIN (rclone/zstd may be absent on PATH) |

### Test coverage of the BROKEN-* rows

`tests/tasks/test_tasks_*.py` covers the **remote** (SSH-and-bash)
ветки end-to-end and the script *builders* unit-style. The local
ветки above have **zero** assertions about Windows behaviour, and
the only Windows-aware test in the tree —
`test_remote_sql_flags_survive_real_bash_tokenization` — is
explicitly skipped on `win32`.

## Approach: tiers from simple to nearly impossible

The principle for every tier: **drop shell from the laptop side
whenever an stdlib equivalent exists; reserve `c.run` strings for
genuinely interactive things.** Bash is for the remote.

### Tier 1 — Trivial swaps (rows 1, 4, 7-`open`, 12)

Pure stdlib substitutions, no behavioural change, ~20-LOC each.

- Row 1 (`pkill`) → `psutil.process_iter()` + `terminate()` filter
  on `cmdline` containing `"uvicorn dinary"`. `psutil` is already
  in the dev dependency tree (transitive via pre-commit / dev
  tools — verify in `pyproject.toml` before claiming so), or add
  it explicitly to dev deps.
- Row 4 (`rm -rf allure-results`) → `shutil.rmtree(Path("allure-results"), ignore_errors=True)`.
- Row 7 (`open -a 'Google Chrome' ...`) → `webbrowser.open(url)`.
  Drops the explicit-Chrome bit; that was always cosmetic.
- Row 12 (`curl ... \|\| echo`) → drop the shell altogether,
  replace with a `urllib.request.urlopen` call wrapped in
  try/except that prints the same fallback message. Keeps semantics
  exact, removes the cmd.exe quote oddity.

Done-criterion for the tier: each task's body has no `c.run` or
`subprocess.run` referencing a Unix-only binary. Unit tests
monkeypatch the stdlib calls and assert call shape.

### Tier 2 — `c.run` argv-isation (rows 2, 3, 8, 11, 13, 14)

The pattern is "we are running a known binary with a known argv;
we are not piping to another shell". Replace
`c.run("foo --bar 'baz qux'")` with
`subprocess.run(["foo", "--bar", "baz qux"], check=True)`. argv
form bypasses cmd.exe entirely on Windows and POSIX shells on
Linux/macOS, so quoting becomes a non-issue everywhere — Windows
support is a free side-effect of fixing the same flag-quoting bug
that bit row 13 / 14 / `527623d62`.

Concretely:

- Row 2: `["uv", "run", "python", "-c", "<payload>"]`.
- Row 3: same shape; the migrate one-liner becomes a list.
- Row 8: `["ssh", host(), "sudo", "journalctl", "-u", "dinary", *flag_args]`.
  `flag` becomes a list (`["-f"]` or `["-n", str(lines), "--no-pager"]`).
  OpenSSH happily concatenates the trailing argv tokens into the
  remote command string itself.
- Row 11: drop the literal `~` and call `os.path.expanduser`
  laptop-side; pass the expanded path to `scp` as argv. Remote
  path keeps `~` (the remote shell expands it).
- Rows 13 / 14: stop building a string at all. Both call sites are
  `uv run python -m dinary.<module> <flags>`; switch to
  `c.run(["uv", "run", "python", "-m", module, *flags])`. This
  retires the same bug class that `527623d62` fixed for the remote
  path. Add the regression test on the local side mirroring
  `test_remote_sql_flags_survive_real_bash_tokenization` but
  asserting on the argv that `c.run` would receive (no real bash
  invocation needed because the hop is gone).

Done-criterion for the tier: zero `c.run("...")` strings in
`tasks/` that contain shell metacharacters (`'`, `"`, `|`, `&&`,
`||`, `~`, `>`) on the laptop-side hot path. Lint rule worth
considering: a tiny ruff plugin or a custom test that greps
`tasks/*.py` for shell-only chars and demands an opt-in comment
on each remaining hit.

### Tier 3 — Replace embedded shell pipelines (rows 5, 6, 16)

These are real shell sequences, not a single binary call. Each
needs a small rewrite, not a one-liner swap.

- Row 5 (`inv uv`): publish two doc paths instead of one shell
  command. On macOS/Linux print + run the existing
  `curl ... \| sh`. On Windows print the official one-liner from
  the `uv` docs (`powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 \| iex"`)
  or simply tell the operator to run `winget install astral-sh.uv`.
  No silent platform branch — print the chosen command first,
  then run it, so the operator sees what's happening.
- Row 6 (`inv ver`): port `scripts/verup.sh` to a tiny Python
  helper (it's a `git tag` + `cz bump` shell, ~20 LOC). The
  shipping shell scripts in `scripts/build-docs.sh`,
  `scripts/upload.sh`, `scripts/build.sh` stay — they are CI
  scripts, not operator scripts.
- Row 16 (`inv backup-cloud-restore`): pure argv. `rclone copyto`,
  `zstd -d`, and `sqlite3` all accept argv lists; the existing
  `subprocess.run(["sqlite3", ...])` already proves that. Replace
  the two `c.run` lines with `subprocess.run([...], check=True)`.
  Remove `shlex.quote`. The tests in
  `tests/tasks/test_tasks_backups_restore.py` already mock
  `subprocess.run` — they continue to work; the existing
  `skipif win32` on the integration test stays because rclone /
  zstd may not be on the operator's PATH on Windows.

Done-criterion: rows 5/6/16 are argv-only. The Windows install
docs for `uv`/`rclone`/`zstd` are linked from `docs/src/en/operations.md`
so the operator knows what to add to PATH.

### Tier 4 — Pseudo-TTY paths (rows 9, 10)

`pty=True` is invoke's way of saying "I want the child to think it
owns a real terminal". Use cases:

- Row 9 (`inv ssh*`): forwards the operator's terminal to a remote
  shell; only useful with a TTY.
- Row 10 (`inv dev`): `uvicorn --reload` colours its log output
  when stdout is a TTY.

invoke's `pty=True` is no-op-with-warning on Windows. Two
options, neither glamorous:

- **Accept the warning.** Document that on Windows the dev server
  log loses colour and `inv ssh` falls back to invoke's plain
  pipe. Existing `winpty` / Windows Terminal users still get
  colour because uvicorn checks `isatty()` itself.
- **Special-case `inv ssh`** to `os.execvp("ssh", ["ssh", host()])`
  on Windows. `execvp` replaces the current process with `ssh.exe`,
  which then talks directly to the parent terminal — no pty
  emulation needed, full TTY semantics through the Windows console.
  `inv dev` is harder to hand off because it needs to run more
  Python afterwards on shutdown; leaving it as the documented
  warning is acceptable.

Done-criterion: warning is documented; `inv ssh` either
hand-offs via `execvp` or carries a banner explaining the limit.

### Tier 5 — Nearly impossible / not worth attempting

- **`inv docs <lang>`** (`tasks/dev.py:40-49`). It assumes
  `scripts/build-docs.sh` (bash) plus `open -a 'Google Chrome'`
  (macOS-only). The bash script itself uses `sed`, `find`, and
  multi-line Bash heredocs to massage the mkdocs config — porting
  it to Python is a real refactor, not a Windows tweak. **Park.**
  Workaround for Windows: run docs from WSL or skip; this is a
  contributor task, not an operator task, so the impact is limited.
- **Interactive Yandex bootstrap** (`tasks/backups.py:243`,
  `ensure_yandex_rclone_configured`). The flow is "type your
  Yandex login, then your app-password, in a real TTY, and the
  bytes are piped over `ssh ... bash`". Works fine over
  PowerShell because `subprocess.run(input=..., text=True)` is OS-
  agnostic when argv-form is used. Audit for any
  `c.run(..., pty=True)` in this path before declaring victory;
  if there isn't one, this is actually Tier 1 — verify and move
  it down.
- **CI Windows matrix.** Adding Windows to GitHub Actions for
  `pytest tests/tasks/` is doable, but the value is small once
  every laptop-side string is argv-form: argv has no quoting and
  the assertions pass identically on every platform. We'd burn CI
  minutes to re-prove what `pytest` already proves. Defer until we
  see a real-world Windows regression slip past argv unit tests.
- **Re-introducing the
  `test_remote_sql_flags_survive_real_bash_tokenization` test on
  Windows.** Requires a real Linux bash on the operator side. WSL's
  `bash.exe` stub fails without a distro; Git-Bash mangles argv via
  MSYS path translation. The skip stays.

## Testing strategy

For Tier 1-3 work, the contract per task is:

- **Unit test in `tests/tasks/`** that monkeypatches
  `subprocess.run` / `c.run` and asserts the argv list that would
  be invoked. argv-list assertions work identically on every OS, so
  no `skipif` decorators are needed. Mirror the shape of
  `tests/tasks/test_tasks_imports.py:_spy_transports`.
- **One end-to-end test** per task that monkeypatches at the
  `subprocess` boundary so the test runs on every CI OS the suite
  already runs on (currently macos-latest, ubuntu-latest,
  windows-latest for the existing matrix per `.github/workflows/`).
- **No new `skipif win32`** — every new test must pass on every
  matrix OS, otherwise the refactor failed.

For Tier 4 (`pty=True`), add a single test that asserts the
documented behaviour ("warns and continues without pty on Windows")
by stubbing `invoke.Context.run` and checking the kwargs forwarded.

## Files to create / modify

Tier 1:
- `tasks/dev.py` — replace `pkill`, `rm -rf`, `open -a`, `curl`.
- `tasks/server.py:58` — replace the `curl \|\| echo` chain.
- `tests/tasks/test_tasks_dev.py` (new) — unit tests for `dev`,
  `test`, `uv` task argv shapes.
- `tests/tasks/test_tasks_server.py` (new) — `status` (local),
  `logs` (remote), `ssh`, `ssh-replica` argv shapes.
- `pyproject.toml` — pin `psutil` in dev deps if not already
  present.

Tier 2:
- `tasks/dev.py:147`, `tasks/db.py:24`, `tasks/server.py:46`,
  `tasks/setup.py:87`, `tasks/reports.py:41`,
  `tasks/imports.py:360` — argv-isation.
- `tests/tasks/test_tasks_reports.py`,
  `tests/tasks/test_tasks_imports.py` — extend with **local-path**
  argv assertions matching the existing remote-path coverage.
- `tests/tasks/test_tasks_db.py` — local `migrate` argv assertion
  (currently only the remote `verify-db` path is tested).

Tier 3:
- `tasks/dev.py:55` — `inv uv` per-platform installer dispatch.
- `tasks/dev.py:27` — replace `verup.sh` shell-out with a Python
  helper (`tasks/_verup.py` or extend `tasks/dev.py`).
- `tasks/reports.py:210`, `tasks/backups.py:507-508` — drop
  `shlex.quote` / `shlex.join`, use argv.
- `docs/src/en/operations.md` — Windows prerequisites section
  listing `uv`, `rclone`, `zstd`, `sqlite3` install commands.

Tier 4:
- `tasks/server.py:71-80` — optional `os.execvp` shortcut on
  Windows for `inv ssh*`.
- `docs/src/en/operations.md` — note about `inv dev` log colour
  on Windows.

## Verification

Per tier, the gate is identical:

1. `uv run inv pre` clean.
2. `uv run pytest` clean on macOS, Linux, **and Windows** in CI.
   No new `skipif win32` decorators added.
3. Manual smoke on each tier: a Windows operator runs the affected
   tasks against a staging VM1; the touched commands complete
   without error or `'Server` style mis-quoting in stdout.

## Open questions

- Do we already have a Windows job in the CI matrix? If not, Tier
  1 lands the first one, and we accept the extra minutes as
  insurance against the next operator who picks up a Windows
  laptop. (Verify in `.github/workflows/` before starting Tier 1.)
- Is there appetite for a tiny ruff plugin / pytest test that
  forbids shell metacharacters in `tasks/*.py` `c.run` strings,
  to keep Tier 2's invariant from regressing? Cheap to add, hard
  to argue against once the rewrite is done.
- Tier 5 audit of `ensure_yandex_rclone_configured`: pure-argv or
  not? If pure-argv, it moves down to Tier 1 and we get the
  Windows-Yandex flow for free.
