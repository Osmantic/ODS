#!/usr/bin/env bash
# ============================================================================
# Regression: macOS `ods config show` (installers/macos/ods-macos.sh) must
# mask secret VALUES, not just keys whose keyword sits immediately before `=`.
# ============================================================================
# The previous `grep -qE "(SECRET|PASS|TOKEN|KEY)="` check only matched when
# the keyword was adjacent to `=`, so keys like OPENCODE_SERVER_PASSWORD,
# LANGFUSE_DB_PASSWORD, LANGFUSE_SALT, N8N_USER and LANGFUSE_INIT_USER_EMAIL
# printed their auto-generated secret values in cleartext. Masking now matches
# the key NAME against the same keyword set the Linux CLI's _cmd_config_is_secret
# falls back to. This test exercises the real script end-to-end.
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CLI="$ROOT_DIR/installers/macos/ods-macos.sh"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'
PASSED=0
FAILED=0
pass() { echo -e "  ${GREEN}✓ PASS${NC} $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "  ${RED}✗ FAIL${NC} $1"; FAILED=$((FAILED + 1)); }

echo ""
echo "=== macOS ods config show — secret masking ==="
echo ""

if [[ ! -f "$CLI" ]]; then
    fail "ods-macos.sh not found at $CLI"
    echo ""; echo "Result: $PASSED passed, $FAILED failed"; exit 1
fi

TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"' EXIT

# `test_install` needs INSTALL_DIR to exist with a compose file, plus a running
# Docker; shim `docker info` so the check passes without Docker Desktop.
SHIM_DIR="$TEMP_DIR/bin"
mkdir -p "$SHIM_DIR"
printf '#!/bin/sh\nexit 0\n' > "$SHIM_DIR/docker"
chmod +x "$SHIM_DIR/docker"

INSTALL_SCAFFOLD="$TEMP_DIR/install"
mkdir -p "$INSTALL_SCAFFOLD"
touch "$INSTALL_SCAFFOLD/docker-compose.base.yml"

cat > "$INSTALL_SCAFFOLD/.env" <<'EOF'
# Test fixture
OLLAMA_PORT=11434
BIND_ADDRESS=127.0.0.1
OPENCODE_SERVER_PASSWORD=leak-pw-value
LANGFUSE_SALT=leak-salt-value
LANGFUSE_DB_PASSWORD=leak-dbpw-value
N8N_USER=leak-user-value
LANGFUSE_INIT_USER_EMAIL=leak@example.test
DASHBOARD_API_KEY=leak-key-value
ODS_SESSION_SECRET=leak-secret-value
LLM_MODEL=some-model
EOF

# Sentinel values whose appearance in stdout would prove a leak.
SECRETS=(leak-pw-value leak-salt-value leak-dbpw-value leak-user-value \
         leak@example.test leak-key-value leak-secret-value)

# Strip ANSI colors so assertions match on plain text.
OUT=$(ODS_HOME="$INSTALL_SCAFFOLD" PATH="$SHIM_DIR:$PATH" \
    bash "$CLI" config show 2>&1 | sed 's/\x1b\[[0-9;]*m//g')

for secret in "${SECRETS[@]}"; do
    if grep -qF "$secret" <<<"$OUT"; then
        fail "secret value LEAKED in cleartext: $secret"
        echo "  --- output ---"; awk '{print "  " $0}' <<<"$OUT"
    else
        pass "secret value masked: $secret"
    fi
done

# The masked keys must still be listed (as KEY=***), not dropped entirely.
for key in OPENCODE_SERVER_PASSWORD LANGFUSE_SALT N8N_USER LANGFUSE_INIT_USER_EMAIL DASHBOARD_API_KEY; do
    if grep -qF "${key}=***" <<<"$OUT"; then
        pass "$key shown as masked ***"
    else
        fail "$key not rendered as ${key}=***"
    fi
done

# Non-secret keys must NOT be masked (no over-mask regression).
for kv in "OLLAMA_PORT=11434" "BIND_ADDRESS=127.0.0.1" "LLM_MODEL=some-model"; do
    if grep -qF "$kv" <<<"$OUT"; then
        pass "non-secret shown in clear: $kv"
    else
        fail "non-secret missing or masked: $kv"
    fi
done

echo ""
echo "Result: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]]
