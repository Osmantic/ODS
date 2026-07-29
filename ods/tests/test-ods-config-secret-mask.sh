#!/usr/bin/env bash
# ============================================================================
# Regression: `ods config show` masks `N8N_USER` and
# `LANGFUSE_INIT_USER_EMAIL` even in environments without `jq`.
# ============================================================================
# Audit follow-up on PR #994 (2026-04-28):
#
#   "Schema-driven secret masking is useful, but the CLI only learns
#    the schema secret flags through `jq`. In Git Bash without `jq`,
#    newly marked user/email fields such as `N8N_USER` and
#    `LANGFUSE_INIT_USER_EMAIL` can still print in clear. Please either
#    make schema parsing available without `jq` for this command or
#    extend the fallback mask to cover the new schema secrets."
#
# Both fixes are now in place: a Python fallback parser when `jq` is
# absent, plus `*user*` / `*email*` keyword fallback when neither is
# present. This test exercises all three PATH configurations.
# ============================================================================

set -euo pipefail

# ods-cli requires Bash 4+; macOS ships Bash 3.2, so "$BASH" "$ODS_CLI"
# below would hit the CLI's version guard and report 9 misleading failures.
# Re-exec under Homebrew bash when available (mirrors scripts/health-check.sh);
# otherwise skip — without Bash 4+ ods-cli cannot run on this host at all.
if [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
    for _modern_bash in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        if [ -x "$_modern_bash" ] && [ "$("$_modern_bash" -c 'echo "${BASH_VERSINFO[0]}"')" -ge 4 ]; then
            exec "$_modern_bash" "$0" "$@"
        fi
    done
    echo "[SKIP] ods-cli requires Bash 4+; this host only has Bash ${BASH_VERSION} (brew install bash)"
    echo "Result: 0 passed, 0 failed (skipped: no Bash 4+ on host)"
    exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ODS_CLI="$ROOT_DIR/ods-cli"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

PASSED=0
FAILED=0

pass() { echo -e "  ${GREEN}✓ PASS${NC} $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "  ${RED}✗ FAIL${NC} $1"; FAILED=$((FAILED + 1)); }

echo ""
echo "╔═══════════════════════════════════════════════╗"
echo "║   ods config show — secret masking matrix   ║"
echo "╚═══════════════════════════════════════════════╝"
echo ""

if [[ ! -x "$ODS_CLI" ]]; then
    fail "ods-cli not found at $ODS_CLI"
    echo ""; echo "Result: $PASSED passed, $FAILED failed"; exit 1
fi

# Scaffold a hermetic install dir. The schema marks N8N_USER and
# LANGFUSE_INIT_USER_EMAIL as secret:true; .env contains values that
# must NEVER appear in `ods config show` output.
TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"' EXIT

INSTALL_SCAFFOLD="$TEMP_DIR/install"
mkdir -p "$INSTALL_SCAFFOLD"
# check_install requires either docker-compose.base.yml or docker-compose.yml
touch "$INSTALL_SCAFFOLD/docker-compose.base.yml"

cat > "$INSTALL_SCAFFOLD/.env" <<'EOF'
# Test fixture
N8N_USER=actual-admin-username
LANGFUSE_INIT_USER_EMAIL=admin@example.test
ODS_OPERATOR=operator-secret
ODS_VERSION=2.0.0-test
HOST_RAM_GB=32
EOF

cat > "$INSTALL_SCAFFOLD/.env.schema.json" <<'EOF'
{
  "properties": {
    "N8N_USER": {"type": "string", "secret": true},
    "LANGFUSE_INIT_USER_EMAIL": {"type": "string", "secret": true},
    "ODS_OPERATOR": {"type": "string", "secret": true},
    "ODS_VERSION": {"type": "string"},
    "HOST_RAM_GB": {"type": "string"}
  }
}
EOF

# Sentinel values whose appearance in stdout would prove a leak.
SECRET_USER='actual-admin-username'
SECRET_EMAIL='admin@example.test'
SECRET_OPERATOR='operator-secret'

run_ods_config_show() {
    # Invokes ods-cli with a controlled PATH to simulate environments
    # with/without jq + python3. NO_COLOR=1 keeps output ASCII.
    local _path="$1"
    local _label="$2"
    local _output
    _output=$(NO_COLOR=1 PATH="$_path" ODS_HOME="$INSTALL_SCAFFOLD" \
        "$BASH" "$ODS_CLI" config show 2>&1)
    echo "$_output"
}

# Discover real paths to bash, sed, awk, mktemp, etc. so the CLI runs.
# We strip jq and/or python3 from PATH by listing only their needed
# siblings. The simplest approach: build a path that excludes a
# specific binary by symlinking required binaries into a tempdir.
build_pathdir_excluding() {
    # build_pathdir_excluding "<exclude1> <exclude2> ..."
    local _excludes="$1"
    local _pdir="$TEMP_DIR/pathdir-$RANDOM"
    mkdir -p "$_pdir"
    local _bin
    # Tools ods-cli (the section we exercise) actually uses.
    for _bin in bash sh ls cat grep sed awk tr cut sort head tail \
                printf echo mkdir rm tee dirname basename pwd \
                python3 jq find env; do
        local _real
        if [[ "$_bin" == "python3" ]]; then
            _real="$(type -P python3 2>/dev/null || true)"
            if [[ "$_real" == *WindowsApps* ]] && type -P python >/dev/null 2>&1; then
                _real="$(type -P python)"
            fi
        else
            _real="$(type -P "$_bin" 2>/dev/null || true)"
        fi
        [[ -z "$_real" ]] && continue
        # Skip excluded names.
        case " $_excludes " in *" $_bin "*) continue ;; esac
        # Use wrappers instead of `ln -s`: on Git Bash, symlinking MSYS
        # binaries into a temp PATH can create executable copies that cannot
        # find their runtime DLLs once /usr/bin is intentionally absent.
        local _escaped="${_real//\\/\\\\}"
        _escaped="${_escaped//\"/\\\"}"
        printf '#!/bin/sh\nexec "%s" "$@"\n' "$_escaped" > "$_pdir/$_bin"
        chmod +x "$_pdir/$_bin"
    done
    echo "$_pdir"
}

# --- Case 1: jq + python3 both present (schema-driven path) ---
PATH_FULL=$(build_pathdir_excluding "")
out1=$(run_ods_config_show "$PATH_FULL" "full")
if grep -q "N8N_USER=\\*\\*\\*" <<<"$out1" && ! grep -qF "$SECRET_USER" <<<"$out1"; then
    pass "with jq+python3: N8N_USER masked, value not leaked"
else
    fail "with jq+python3: N8N_USER not masked correctly"
    echo "  --- output ---"; awk '{print "  " $0}' <<<"$out1"
fi
if grep -q "LANGFUSE_INIT_USER_EMAIL=\\*\\*\\*" <<<"$out1" && ! grep -qF "$SECRET_EMAIL" <<<"$out1"; then
    pass "with jq+python3: LANGFUSE_INIT_USER_EMAIL masked, value not leaked"
else
    fail "with jq+python3: LANGFUSE_INIT_USER_EMAIL not masked correctly"
    echo "  --- output ---"; awk '{print "  " $0}' <<<"$out1"
fi

if grep -q "ODS_OPERATOR=\\*\\*\\*" <<<"$out1" && ! grep -qF "$SECRET_OPERATOR" <<<"$out1"; then
    pass "with jq+python3: non-keyword schema secret masked"
else
    fail "with jq+python3: non-keyword schema secret not masked correctly"
    echo "  --- output ---"; awk '{print "  " $0}' <<<"$out1"
fi

# --- Case 2: no jq, python3 present (Git-Bash-without-jq simulation) ---
PATH_NO_JQ=$(build_pathdir_excluding "jq")
out2=$(run_ods_config_show "$PATH_NO_JQ" "no-jq")
if grep -q "N8N_USER=\\*\\*\\*" <<<"$out2" && ! grep -qF "$SECRET_USER" <<<"$out2"; then
    pass "without jq: N8N_USER masked via python3 fallback"
else
    fail "without jq: N8N_USER LEAKED — Git Bash regression"
    echo "  --- output ---"; awk '{print "  " $0}' <<<"$out2"
fi
if grep -q "LANGFUSE_INIT_USER_EMAIL=\\*\\*\\*" <<<"$out2" && ! grep -qF "$SECRET_EMAIL" <<<"$out2"; then
    pass "without jq: LANGFUSE_INIT_USER_EMAIL masked via python3 fallback"
else
    fail "without jq: LANGFUSE_INIT_USER_EMAIL LEAKED — Git Bash regression"
    echo "  --- output ---"; awk '{print "  " $0}' <<<"$out2"
fi

if grep -q "ODS_OPERATOR=\\*\\*\\*" <<<"$out2" && ! grep -qF "$SECRET_OPERATOR" <<<"$out2"; then
    pass "without jq: non-keyword schema secret masked via python3 fallback"
else
    fail "without jq: non-keyword schema secret LEAKED - python3 fallback did not load"
    echo "  --- output ---"; awk '{print "  " $0}' <<<"$out2"
fi

# --- Case 3: neither jq nor python3 (keyword-fallback only) ---
PATH_NO_TOOLS=$(build_pathdir_excluding "jq python3")
out3=$(run_ods_config_show "$PATH_NO_TOOLS" "no-tools")
if grep -q "N8N_USER=\\*\\*\\*" <<<"$out3" && ! grep -qF "$SECRET_USER" <<<"$out3"; then
    pass "without jq+python3: N8N_USER masked via *user* keyword"
else
    fail "without jq+python3: N8N_USER LEAKED — keyword fallback gap"
    echo "  --- output ---"; awk '{print "  " $0}' <<<"$out3"
fi
if grep -q "LANGFUSE_INIT_USER_EMAIL=\\*\\*\\*" <<<"$out3" && ! grep -qF "$SECRET_EMAIL" <<<"$out3"; then
    pass "without jq+python3: LANGFUSE_INIT_USER_EMAIL masked via *user*/*email* keyword"
else
    fail "without jq+python3: LANGFUSE_INIT_USER_EMAIL LEAKED — keyword fallback gap"
    echo "  --- output ---"; awk '{print "  " $0}' <<<"$out3"
fi

# --- Sanity: non-secret keys are NOT masked (no over-mask regression) ---
if grep -q "ODS_VERSION=2.0.0-test" <<<"$out1"; then
    pass "non-secret ODS_VERSION shown in clear (no over-mask)"
else
    fail "non-secret ODS_VERSION incorrectly masked or missing"
fi

echo ""
echo "Result: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]]
