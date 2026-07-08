#!/usr/bin/env bash
# ============================================================================
# Regression: bootstrap-upgrade.sh's OpenClaw recreate path must guard on
# both ${#COMPOSE_ARGS[@]} > 0 AND -n "$DOCKER_COMPOSE_CMD" before
# expanding $DOCKER_COMPOSE_CMD as the command word.
# ============================================================================
# Audit follow-up on PR #974 (Lightheartdevs, 2026-04-28):
#
#   "The direction of using $DOCKER_CMD instead of bare docker is right,
#    but the OpenClaw recreation path can still invoke an empty compose
#    command when no compose binary is available. Please make that path
#    use the same compose detection/failure handling contract as the rest
#    of the installer before merge."
#
# Without the second guard, when DOCKER_COMPOSE_CMD is empty (no compose
# v2 plugin AND no docker-compose v1 binary) but COMPOSE_ARGS is populated,
# the line `$DOCKER_COMPOSE_CMD "${COMPOSE_ARGS[@]}" up -d ...` expands to
# `"${COMPOSE_ARGS[@]}" up -d ...` and tries to execute the first
# compose-arg (typically `-f`) as a binary. This test locks in the fix.
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET="$ROOT_DIR/scripts/bootstrap-upgrade.sh"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

PASSED=0
FAILED=0

pass() { echo -e "  ${GREEN}✓ PASS${NC} $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "  ${RED}✗ FAIL${NC} $1"; FAILED=$((FAILED + 1)); }

echo ""
echo "╔═══════════════════════════════════════════════╗"
echo "║   bootstrap-upgrade — OpenClaw compose guard  ║"
echo "╚═══════════════════════════════════════════════╝"
echo ""

if [[ ! -f "$TARGET" ]]; then
    fail "bootstrap-upgrade.sh not found at $TARGET"
    echo ""; echo "Result: $PASSED passed, $FAILED failed"; exit 1
fi
pass "bootstrap-upgrade.sh exists"

# 1. Source-pattern check: the OpenClaw recreate block must contain the
#    canonical guard pattern. Extract the block via awk between the
#    "Recreating OpenClaw" log line and the closing fi for that block.
openclaw_block=$(awk '
    /Recreating OpenClaw to pick up model change/ { in_block=1 }
    in_block { print }
    in_block && /^[[:space:]]+fi[[:space:]]*$/ { fi_count++; if (fi_count == 2) exit }
' "$TARGET")

if [[ -z "$openclaw_block" ]]; then
    fail "could not extract OpenClaw recreate block from $TARGET"
    echo ""; echo "Result: $PASSED passed, $FAILED failed"; exit 1
fi

# Strip comment lines before grepping so the canonical-pattern check
# matches the actual `if [[ ... ]]` statement, not a literal mention
# of the guard inside the rationale comment block. Without this, a
# future PR that rewrites the comment but breaks the code would
# silently pass case #2.
openclaw_code=$(grep -v '^[[:space:]]*#' <<<"$openclaw_block")

# Guard literal — matches the canonical pattern used by the
# llama-server hot-swap blocks earlier in the file. Single-quoted on
# purpose: we want the literal $-bearing string for grep, not an
# expansion. Anchored to the `if [[ ... ]];` structural form so a
# pasted-into-comment occurrence cannot satisfy this check.
# shellcheck disable=SC2016
canonical_if='if [[ ${#COMPOSE_ARGS[@]} -gt 0 && -n "$DOCKER_COMPOSE_CMD" ]];'
if grep -qF "$canonical_if" <<<"$openclaw_code"; then
    pass "OpenClaw recreate guards on BOTH \${#COMPOSE_ARGS[@]} > 0 AND -n \"\$DOCKER_COMPOSE_CMD\""
else
    fail "OpenClaw recreate missing canonical guard (in active code, not just comments)"
    echo "  --- code (comments stripped) ---"
    awk '{print "  " $0}' <<<"$openclaw_code"
fi

# 2. Source-pattern check: the block must NOT use the single-condition
#    guard `${#COMPOSE_ARGS[@]} -gt 0` immediately followed by an
#    unguarded $DOCKER_COMPOSE_CMD invocation. This catches a regression
#    where someone re-introduces the half-guard.
single_guard_only='if \[\[ \$\{#COMPOSE_ARGS\[@\]\} -gt 0 \]\]; then$'
if grep -qE "$single_guard_only" <<<"$openclaw_block"; then
    fail "OpenClaw recreate uses single-condition guard (regression)"
else
    pass "OpenClaw recreate does not use the half-guard form"
fi

# 3. Behavioural simulation of the guard logic: with DOCKER_COMPOSE_CMD
#    empty but COMPOSE_ARGS populated, the OLD guard would have tried to
#    execute the first compose-arg as a command. The NEW guard must
#    short-circuit to the warning branch.
old_branch=""; new_branch=""
COMPOSE_ARGS=(-f /tmp/example-base.yml -f /tmp/example-nvidia.yml)
DOCKER_COMPOSE_CMD=""

# Old (buggy) form
if [[ ${#COMPOSE_ARGS[@]} -gt 0 ]]; then
    old_branch="execute"
else
    old_branch="warn"
fi

# New (fixed) form
if [[ ${#COMPOSE_ARGS[@]} -gt 0 && -n "$DOCKER_COMPOSE_CMD" ]]; then
    new_branch="execute"
else
    new_branch="warn"
fi

if [[ "$old_branch" == "execute" && "$new_branch" == "warn" ]]; then
    pass "guard logic: old form would execute, new form correctly warns"
else
    fail "guard logic: old=$old_branch new=$new_branch (expected old=execute new=warn)"
fi

# 4. Behavioural simulation of the happy path: with both COMPOSE_ARGS
#    populated AND DOCKER_COMPOSE_CMD set, the new guard must let
#    execution through (no false-negative).
COMPOSE_ARGS=(-f /tmp/example-base.yml)
DOCKER_COMPOSE_CMD="docker compose"

happy_branch=""
if [[ ${#COMPOSE_ARGS[@]} -gt 0 && -n "$DOCKER_COMPOSE_CMD" ]]; then
    happy_branch="execute"
else
    happy_branch="warn"
fi

if [[ "$happy_branch" == "execute" ]]; then
    pass "guard logic: happy path correctly proceeds to execute"
else
    fail "guard logic: happy path incorrectly warned (false negative)"
fi

# 5. Behavioural simulation of zero-args: COMPOSE_ARGS empty must also warn.
COMPOSE_ARGS=()
DOCKER_COMPOSE_CMD="docker compose"

empty_branch=""
if [[ ${#COMPOSE_ARGS[@]} -gt 0 && -n "$DOCKER_COMPOSE_CMD" ]]; then
    empty_branch="execute"
else
    empty_branch="warn"
fi

if [[ "$empty_branch" == "warn" ]]; then
    pass "guard logic: empty COMPOSE_ARGS correctly warns"
else
    fail "guard logic: empty COMPOSE_ARGS incorrectly executed"
fi

echo ""
echo "Result: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]]
