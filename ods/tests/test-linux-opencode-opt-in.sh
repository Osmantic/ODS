#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE="$ROOT/installers/phases/07-devtools.sh"
CORE="$ROOT/install-core.sh"

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    exit 1
}

run_dry_phase() {
    local enabled="$1"
    (
        SCRIPT_DIR="$ROOT"
        DRY_RUN=true
        ENABLE_OPENCODE="$enabled"
        ods_progress() { :; }
        log() { printf '%s\n' "$*"; }
        # shellcheck disable=SC1090
        source "$PHASE"
    )
}

off_output="$(run_dry_phase false)"
on_output="$(run_dry_phase true)"

[[ "$off_output" == *"OpenCode extension is disabled; it would not be installed or started"* ]] \
    || fail "dry-run does not explain that OpenCode is disabled"
[[ "$off_output" != *"Would install and configure the optional OpenCode"* ]] \
    || fail "disabled dry-run still plans an OpenCode installation"
[[ "$on_output" == *"Would install and configure the optional OpenCode browser IDE"* ]] \
    || fail "explicit OpenCode opt-in is missing from the dry-run plan"

grep -Fq 'ENABLE_OPENCODE=false' "$CORE" \
    || fail "OpenCode is not disabled by default"
grep -Fq -- '--opencode) ENABLE_OPENCODE=true' "$CORE" \
    || fail "--opencode does not enable the extension"
grep -Fq -- '--no-opencode) ENABLE_OPENCODE=false' "$CORE" \
    || fail "--no-opencode does not disable the extension"
grep -Fq 'systemctl --user disable --now opencode-web.service' "$PHASE" \
    || fail "a disabling rerun cannot retire the ODS-managed OpenCode service"

printf '[PASS] Linux OpenCode is opt-in and reversibly managed\n'
