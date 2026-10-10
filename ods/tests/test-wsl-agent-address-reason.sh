#!/usr/bin/env bash
# The address helper reports a machine-readable reason. The caller must name it,
# so a failed WSL networking probe is not sent to the same .env-ownership hint
# as an unrelated failure.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
real_python3="$(command -v python3)"

fixture="$(mktemp -d "$ROOT/tests/.wsl-agent-reason.XXXXXXXX")"
[[ "$fixture" == "$ROOT"/tests/.wsl-agent-reason.* && -d "$fixture" ]]
trap 'rm -rf -- "$fixture"' EXIT

mkdir -p "$fixture/install/lib"
: > "$fixture/install/lib/wsl-agent-address.py"

# shellcheck source=../lib/wsl-agent-address.sh
. "$ROOT/lib/wsl-agent-address.sh"

# Inert OS boundary: the helper only prepares the address on Linux.
uname() { printf 'Linux\n'; }

# Emit a chosen payload for the helper, and delegate the caller's own JSON
# extraction to the real interpreter.
python3() {
    if [[ "$1" == "$fixture/install/lib/wsl-agent-address.py" ]]; then
        [[ -z "${payload:-}" ]] || printf '%s\n' "$payload"
        return 2
    fi
    "$real_python3" "$@"
}

check() { # check <expected-reason> <payload>
    local expected="$1" payload="$2" rc=0 message
    message=$(ods_prepare_wsl_agent_address "$fixture/install" 2>&1) || rc=$?
    if [[ "$rc" != 1 ]]; then
        printf 'FAIL: expected rc=1 for %s, got %s\n' "${payload:-<empty>}" "$rc"
        exit 1
    fi
    if [[ "$message" != *"(reason: ${expected})"* ]]; then
        printf 'FAIL: %s did not name reason %s: %s\n' "${payload:-<empty>}" "$expected" "$message"
        exit 1
    fi
    printf 'PASS: named reason %s for %s\n' "$expected" "${payload:-<empty>}"
}

check networking '{"error": "networking"}'
check address '{"error": "address"}'
check root '{"error": "root"}'
check internal '{"error": "internal"}'
check unknown 'not json at all'
check unknown ''
check unknown '{"changed": false, "mode": "unmanaged"}'
check unknown '{"error": {"nested": 1}}'
check unknown '{"error": "Bogus"}'
check unknown '{"error": "averyveryverylongreasonthatexceedsthelimit"}'
