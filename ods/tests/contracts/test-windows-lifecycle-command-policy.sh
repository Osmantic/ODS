#!/usr/bin/env bash
# Contract: every Windows lifecycle command that ODS prints or documents runs
# under Windows' default execution policy.
#
# Owners copy these commands into a new PowerShell window, often days after
# setup. There the README's `Set-ExecutionPolicy -Scope Process` no longer
# applies, and a source tree extracted from the downloaded ZIP carries the
# downloaded-file mark, which RemoteSigned blocks too. `powershell -File` or
# `& .\script.ps1` then fails with "running scripts is disabled on this
# system". The process-scoped `-ExecutionPolicy Bypass` (as #7829 added to the
# durable startup.ps1 line) changes no persistent setting.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPO_DIR="$(cd "$ROOT_DIR/.." && pwd)"
FAILURES=0

fail() {
    echo "[FAIL] $*" >&2
    FAILURES=$((FAILURES + 1))
}

pass() {
    echo "[PASS] $*"
}

docs=()
while IFS= read -r file; do
    docs+=("$file")
done < <(find "$ROOT_DIR/docs" "$ROOT_DIR/installers" -name '*.md' -type f; \
         find "$ROOT_DIR" "$REPO_DIR" -maxdepth 1 -name '*.md' -type f)

scripts=("$REPO_DIR/install.ps1")
while IFS= read -r file; do
    scripts+=("$file")
done < <(find "$ROOT_DIR/installers" -name '*.ps1' -type f -not -path '*/tests/*')

# A documented `powershell ... -File` must carry the process-scoped bypass.
hits="$(grep -nE '(powershell|pwsh)(\.exe)?\b.*-File' "${docs[@]}" | grep -v 'ExecutionPolicy Bypass' || true)"
if [[ -n "$hits" ]]; then
    fail "documented powershell -File commands without -ExecutionPolicy Bypass:"$'\n'"$hits"
else
    pass "documented powershell -File commands carry -ExecutionPolicy Bypass"
fi

# The lifecycle helpers are run later, from a new window: never document a
# direct call that depends on the setup session's policy.
hits="$(grep -nE '^[[:space:]]*&[[:space:]].*(wsl-lifecycle|startup)\.ps1' "${docs[@]}" || true)"
if [[ -n "$hits" ]]; then
    fail "documented direct calls of lifecycle scripts:"$'\n'"$hits"
else
    pass "lifecycle scripts are documented through powershell -File"
fi

# Commands the installers print for the owner to copy.
hits="$(grep -nE 'Write-(Host|Warning|Output).*(powershell|pwsh)(\.exe)?\b.*-File' "${scripts[@]}" \
    | grep -v 'ExecutionPolicy Bypass' || true)"
if [[ -n "$hits" ]]; then
    fail "printed powershell -File commands without -ExecutionPolicy Bypass:"$'\n'"$hits"
else
    pass "printed powershell -File commands carry -ExecutionPolicy Bypass"
fi

if (( FAILURES > 0 )); then
    echo "$FAILURES lifecycle command contract(s) failed" >&2
    exit 1
fi
