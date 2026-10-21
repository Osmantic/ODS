#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source "$ROOT/installers/lib/preflight-jq.sh"

tmp=$(mktemp -d)
cleanup() {
    rm -f -- "$tmp/dry-run.out" "$tmp/install.out" "$tmp/privilege-calls" "$tmp/jq-installed"
    rmdir -- "$tmp"
}
trap cleanup EXIT
log() { :; }
error() { printf '%s\n' "$*" >&2; return 1; }
ods_sudo_available() { printf 'privilege-check\n' >> "$tmp/privilege-calls"; return 0; }
ods_sudo() { printf '%s\n' "$*" >> "$tmp/privilege-calls"; return 0; }
command() {
    if [[ "${1:-}" == -v && "${2:-}" == jq ]]; then [[ -f "$tmp/jq-installed" ]]; return; fi
    builtin command "$@"
}

DRY_RUN=true PKG_MANAGER=apt
if ods_preflight_require_jq >"$tmp/dry-run.out" 2>&1; then
    echo 'dry run accepted a missing required jq' >&2; exit 1
fi
grep -Fq 'dry run will not install packages' "$tmp/dry-run.out"
if [[ -e "$tmp/privilege-calls" ]]; then
    echo 'dry run attempted a privileged package operation' >&2; exit 1
fi

DRY_RUN=false
if ods_preflight_require_jq >"$tmp/install.out" 2>&1; then
    echo 'real install accepted a still-missing jq' >&2; exit 1
fi
grep -Fxq 'privilege-check' "$tmp/privilege-calls"
grep -Fxq 'apt-get update -qq' "$tmp/privilege-calls"
grep -Fxq 'apt-get install -y jq' "$tmp/privilege-calls"
printf '%s\n' 'installer dry-run jq prerequisite tests passed'

# Void is detected as xbps before phase 01, so a clean machine without jq
# must not fall through to Debian's package manager. All privilege calls are
# test doubles; no host package command is invoked.
PKG_MANAGER=xbps
jq() { printf 'jq-test\n'; }
ods_sudo() {
    printf '%s\n' "$*" >> "$tmp/privilege-calls"
    [[ "$*" == 'xbps-install -Sy jq' ]] || return 127
    [[ "${XBPS_FAIL:-false}" != true ]] || return 23
    : > "$tmp/jq-installed"
}
: > "$tmp/privilege-calls"
ods_preflight_require_jq >"$tmp/install.out" 2>&1
grep -Fxq 'xbps-install -Sy jq' "$tmp/privilege-calls"
if grep -Fq 'apt-get' "$tmp/privilege-calls"; then
    echo 'Void preflight invoked apt-get' >&2; exit 1
fi

rm "$tmp/jq-installed"
: > "$tmp/privilege-calls"
XBPS_FAIL=true
if ods_preflight_require_jq >"$tmp/install.out" 2>&1; then
    echo 'Void preflight accepted a failed jq install' >&2; exit 1
fi
grep -Fq 'Failed to install jq automatically' "$tmp/install.out"
[[ ! -f "$tmp/jq-installed" ]]

for guard in dry-run preflight-only; do
    : > "$tmp/privilege-calls"
    DRY_RUN=false PREFLIGHT_ONLY=false
    if [[ "$guard" == dry-run ]]; then DRY_RUN=true; else PREFLIGHT_ONLY=true; fi
    if ods_preflight_require_jq >"$tmp/install.out" 2>&1; then
        [[ "$guard" == preflight-only ]]
    else
        [[ "$guard" == dry-run ]]
    fi
    [[ ! -s "$tmp/privilege-calls" ]]
done
printf '%s\n' 'Void jq install, failure and read-only preflight tests passed'
