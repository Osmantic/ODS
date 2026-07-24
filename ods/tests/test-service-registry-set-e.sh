#!/bin/bash
# Regression test: sr_compose_flags must not abort a `set -e` caller.
#
# sr_compose_flags bumps _SR_CACHE_HITS / _SR_CACHE_MISSES. When those used
# ((counter++)), the first bump (0 -> 1) evaluates to the pre-increment value
# 0, so the arithmetic command returned exit 1. A caller running under
# `set -e` that invokes sr_compose_flags directly (not inside $(...), which
# masks the failure) would abort mid-function.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_DIR="$SCRIPT_DIR/.."
SERVICE_REGISTRY="$ODS_DIR/lib/service-registry.sh"

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

pass() { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; exit 1; }
info() { echo -e "${BLUE}ℹ${NC} $1"; }

[[ -f "$SERVICE_REGISTRY" ]] || fail "service-registry.sh not found at $SERVICE_REGISTRY"

info "Test: sr_compose_flags survives a direct call under set -e"

# Run in a child bash under `set -e`. sr_load is stubbed out (via _SR_LOADED
# and an empty SERVICE_IDS) so the test needs neither PyYAML nor real
# manifests — it exercises only the counter/return paths. The calls are NOT
# wrapped in $(...), so a failing arithmetic command would abort the child.
output="$(
    bash -c '
        set -euo pipefail
        # shellcheck disable=SC1090
        source "'"$SERVICE_REGISTRY"'"
        _SR_LOADED=true
        SERVICE_IDS=()
        sr_compose_flags >/dev/null   # cache miss: bumps _SR_CACHE_MISSES 0 -> 1
        sr_compose_flags >/dev/null   # cache hit:  bumps _SR_CACHE_HITS   0 -> 1
        echo "SURVIVED hits=${_SR_CACHE_HITS} misses=${_SR_CACHE_MISSES}"
    '
)" || fail "sr_compose_flags aborted its set -e caller (arithmetic increment returned non-zero)"

[[ "$output" == "SURVIVED hits=1 misses=1" ]] || fail "unexpected counter state: '$output'"
pass "sr_compose_flags does not abort under set -e (counters: hits=1 misses=1)"

echo ""
pass "All service-registry set -e tests passed"
