#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source "$ROOT/installers/lib/preflight-jq.sh"

tmp=$(mktemp -d)
cleanup() {
    rm -f -- "$tmp/dry-run.out" "$tmp/install.out" "$tmp/privilege-calls"
    rmdir -- "$tmp"
}
trap cleanup EXIT
log() { :; }
error() { printf '%s\n' "$*" >&2; return 1; }
ods_sudo_available() { printf 'privilege-check\n' >> "$tmp/privilege-calls"; return 0; }
ods_sudo() { printf '%s\n' "$*" >> "$tmp/privilege-calls"; return 0; }
command() {
    if [[ "${1:-}" == -v && "${2:-}" == jq ]]; then return 1; fi
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
