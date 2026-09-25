#!/bin/bash
# ============================================================================
# Test: installers/phases/09-offline.sh warns when clearing cloud API keys
#       fails, instead of silently swallowing the failure.
# ============================================================================
# Regression for: offline mode's ".env cleared, safe to disconnect" claim
# was previously unconditional even when the underlying `_sed_i` calls that
# clear BRAVE_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY failed, because
# each call was followed by `2>/dev/null || true`.
#
# This test extracts the actual key-clearing block from the phase file (both
# the pre-fix version via `git show HEAD` and whatever is currently on disk)
# and runs each one directly against a target that forces `_sed_i` to fail,
# with a stub `ai_warn` that records whether it was called.
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PHASE_FILE="$REPO_ROOT/installers/phases/09-offline.sh"

PASS=0
FAIL=0

check() {
    local desc="$1" actual="$2" expected="$3"
    if [[ "$actual" == "$expected" ]]; then
        echo "  PASS: $desc"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $desc (expected '$expected', got '$actual')"
        FAIL=$((FAIL + 1))
    fi
}

# ----------------------------------------------------------------------------
# Extract the key-clearing block: from the "_offline_cleared_keys=true" (or,
# pre-fix, the first "_sed_i ... BRAVE_API_KEY" line) through the closing
# marker of that section, so we run the exact production logic, not a copy.
# ----------------------------------------------------------------------------
extract_current_block() {
    sed -n '/_offline_cleared_keys=true/,/^    fi$/p' "$PHASE_FILE"
}

extract_prefix_block() {
    # `git show <rev>:<path>` always resolves <path> from the repo top
    # level, never from cwd — so prepend the show-prefix explicitly.
    local prefix
    prefix="$(cd "$REPO_ROOT" && git rev-parse --show-prefix)"
    (cd "$REPO_ROOT" && git show "HEAD:${prefix}installers/phases/09-offline.sh") \
        | sed -n '/BRAVE_API_KEY=.*BRAVE_API_KEY=/,/OPENAI_API_KEY=.*OPENAI_API_KEY=.*true/p'
}

run_block() {
    local block="$1" env_file="$2" warn_log="$3"
    (
        # shellcheck disable=SC2317
        ai_warn() { printf '%s\n' "$1" >> "$warn_log"; }
        _sed_i() {
            if sed --version 2>/dev/null | grep -q GNU; then
                sed -i "$@"
            else
                sed -i '' "$@"
            fi
        }
        INSTALL_DIR="$(dirname "$env_file")"
        export INSTALL_DIR
        eval "$block"
    )
}

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "== Pre-fix block: sed target forced to fail =="
FAIL_DIR="$TMP_DIR/prefix-fail"
mkdir -p "$FAIL_DIR"
# .env is a directory, not a file, so `sed -i` on it always errors.
mkdir -p "$FAIL_DIR/.env"
WARN_LOG_PRE="$TMP_DIR/warn-pre.log"
: > "$WARN_LOG_PRE"
set +e
PREFIX_BLOCK="$(extract_prefix_block)"
run_block "$PREFIX_BLOCK" "$FAIL_DIR/.env" "$WARN_LOG_PRE"
PREFIX_RC=$?
set -e
check "pre-fix block does not crash despite sed failure" "$PREFIX_RC" "0"
check "pre-fix block never warns about the failure (the bug)" "$(wc -l < "$WARN_LOG_PRE" | tr -d ' ')" "0"

echo "== Post-fix block: sed target forced to fail =="
FAIL_DIR2="$TMP_DIR/postfix-fail"
mkdir -p "$FAIL_DIR2"
mkdir -p "$FAIL_DIR2/.env"
WARN_LOG_POST="$TMP_DIR/warn-post.log"
: > "$WARN_LOG_POST"
set +e
CURRENT_BLOCK="$(extract_current_block)"
run_block "$CURRENT_BLOCK" "$FAIL_DIR2/.env" "$WARN_LOG_POST"
POSTFIX_RC=$?
set -e
check "post-fix block does not crash (warn is non-fatal)" "$POSTFIX_RC" "0"
check "post-fix block warns exactly once about the failure" "$(wc -l < "$WARN_LOG_POST" | tr -d ' ')" "1"
if [[ -s "$WARN_LOG_POST" ]]; then
    check "warning message names the affected keys" \
        "$(grep -qc 'BRAVE_API_KEY.*ANTHROPIC_API_KEY.*OPENAI_API_KEY' "$WARN_LOG_POST" && echo yes || echo no)" \
        "yes"
fi

echo "== Post-fix block: successful clearing path (no false-positive warn) =="
OK_DIR="$TMP_DIR/postfix-ok"
mkdir -p "$OK_DIR"
cat > "$OK_DIR/.env" << 'EOF'
BRAVE_API_KEY=abc123
ANTHROPIC_API_KEY=def456
OPENAI_API_KEY=ghi789
EOF
WARN_LOG_OK="$TMP_DIR/warn-ok.log"
: > "$WARN_LOG_OK"
run_block "$CURRENT_BLOCK" "$OK_DIR/.env" "$WARN_LOG_OK"
check "post-fix block keeps keys cleared on the success path" \
    "$(grep -c '^\(BRAVE\|ANTHROPIC\|OPENAI\)_API_KEY=$' "$OK_DIR/.env")" "3"
check "post-fix block does not warn when clearing succeeds" "$(wc -l < "$WARN_LOG_OK" | tr -d ' ')" "0"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
