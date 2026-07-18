#!/bin/bash
# Provision a machine (CI runner, container, fresh checkout, or an agent
# session) so BOTH gates pass out of the box:
#   uv run inv pre    (ruff, ruff-format, pyrefly)
#   uv run pytest     (the FULL suite — analytics + backup tests included)
#
# A bare `uv sync` is not enough. The suite additionally needs:
#   1. Every dependency group (dev + analytics). The analytics tests import
#      duckdb / lmdb / marimo / mcp / altair / polars; without them those
#      tests fail to collect and pyrefly reports missing-import errors.
#   2. The `zstd` and `sqlite3` CLIs. The backup/restore tasks and their
#      tests shell out to these real binaries.
#
# Idempotent and non-interactive: safe to re-run any time.
#
# Usage:  bash scripts/setup-test-env.sh
set -euo pipefail

cd "$(dirname "$0")/.."

# 1. Python deps: every dependency group, so the full test suite resolves.
echo "setup-test-env: uv sync --all-groups"
uv sync --all-groups

# 2. System binaries the backup/restore tests invoke directly.
missing=()
for bin in zstd sqlite3; do
  command -v "$bin" >/dev/null 2>&1 || missing+=("$bin")
done
if [ "${#missing[@]}" -gt 0 ]; then
  echo "setup-test-env: installing missing binaries: ${missing[*]}"
  sudo_cmd=""
  [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1 && sudo_cmd="sudo"
  if command -v apt-get >/dev/null 2>&1; then
    $sudo_cmd apt-get update -qq
    $sudo_cmd apt-get install -y -qq "${missing[@]}"
  else
    echo "setup-test-env: WARNING no apt-get; install ${missing[*]} manually" >&2
    exit 1
  fi
fi

echo "setup-test-env: environment ready"
