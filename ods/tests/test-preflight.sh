#!/bin/bash
# ============================================================================
# ODS ods-preflight.sh Test Suite
# ============================================================================
# Validates that ods-preflight.sh is syntactically correct, follows the
# project's shell style requirements, and encodes the correct LLM port
# default (8080, not 11434).
#
# These tests are static (no running Docker) — they inspect the script itself.
# Integration-level tests (actual service probing) are covered in
# tests/test-health-check.sh and tests/test-integration.sh.
#
# Usage: bash tests/test-preflight.sh
# Exit codes: 0 = all pass, 1 = one or more failures
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PREFLIGHT="$ROOT_DIR/ods-preflight.sh"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASSED=0
FAILED=0

pass() { printf "  ${GREEN}✓ PASS${NC} %s\n" "$1"; PASSED=$((PASSED + 1)); }
fail() { printf "  ${RED}✗ FAIL${NC} %s\n" "$1"; FAILED=$((FAILED + 1)); }
skip() { printf "  ${YELLOW}⊘ SKIP${NC} %s\n" "$1"; }

echo ""
echo "╔════════════════════════════════════════════════╗"
echo "║   ods-preflight.sh Test Suite               ║"
echo "╚════════════════════════════════════════════════╝"
echo ""

# ── Static checks ──────────────────────────────────────────────────────────

# 1. Script exists
if [[ ! -f "$PREFLIGHT" ]]; then
    fail "ods-preflight.sh not found at $PREFLIGHT"
    echo ""
    echo "Result: $PASSED passed, $FAILED failed"
    exit 1
fi
pass "ods-preflight.sh exists"

# 2. Script passes bash syntax check
if bash -n "$PREFLIGHT" 2>/dev/null; then
    pass "bash -n syntax check passes"
else
    fail "bash -n syntax check failed"
fi

# 3. Uses set -euo pipefail (project style requirement per CONTRIBUTING.md)
if grep -q 'set -euo pipefail' "$PREFLIGHT"; then
    pass "set -euo pipefail present"
else
    fail "set -euo pipefail missing — CONTRIBUTING.md requires this in all bash files"
fi

# 4. LLM port default is 8080, NOT 11434
# config/ports.json and docker-compose.base.yml define the canonical default as 8080.
# 11434 is only the Strix Halo AMD override (written to .env by phase 06).
# The fallback in the script must be 8080 so plain installs without OLLAMA_PORT in
# .env get the correct port probed.
if grep -q 'LLAMA_SERVER_PORT:-8080' "$PREFLIGHT"; then
    pass "LLM port fallback is 8080 (aligns with config/ports.json)"
else
    fail "LLM port fallback is not 8080 — check the OLLAMA_PORT expansion in ods-preflight.sh"
fi

# 5. Does NOT contain the old wrong fallback of 11434 in port resolution
if grep -q 'LLAMA_SERVER_PORT:-11434' "$PREFLIGHT"; then
    fail "Old wrong LLM port fallback 11434 still present — should be 8080"
else
    pass "Old wrong LLM port fallback 11434 is gone"
fi

# 6. detect_backend function is present
if grep -q 'detect_backend()' "$PREFLIGHT"; then
    pass "detect_backend function present"
else
    fail "detect_backend function missing"
fi

# 7. AMD sysfs scan iterates all DRM cards (not just card1)
if grep -q '/sys/class/drm/card\*/device' "$PREFLIGHT"; then
    pass "AMD sysfs scan uses glob (all DRM cards)"
else
    fail "AMD sysfs scan missing wildcard — may miss some AMD GPUs"
fi

# 8. Script uses BASH_SOURCE for portability (not $0)
if grep -q 'BASH_SOURCE' "$PREFLIGHT"; then
    pass "Uses BASH_SOURCE for script dir resolution"
else
    fail "Missing BASH_SOURCE — \$0 breaks when script is sourced"
fi

# 9. Summary section has all expected check sections
for check_label in "Checking Docker" "Checking Docker Compose" "Checking GPU" \
                   "Checking LLM endpoint" "Checking Whisper" "Checking TTS" \
                   "Checking Embeddings" "Checking Dashboard"; do
    if grep -q "$check_label" "$PREFLIGHT"; then
        pass "Check section present: $check_label"
    else
        fail "Check section missing: $check_label"
    fi
done

# 10. Uses docker port to probe actual external port mapping (not just hardcoded)
if grep -q 'docker port ods-llama-server' "$PREFLIGHT"; then
    pass "Probes actual Docker port mapping via 'docker port'"
else
    fail "Does not probe actual Docker port mapping"
fi

# 11. A host-native llama-server (Windows Portal) checks LiteLLM, not the
# in-stack llama-server container
if grep -q 'ods_preflight_host_native_llm()' "$SCRIPT_DIR/../lib/preflight-llm-route.sh" \
   && grep -q 'if ods_preflight_uses_litellm; then' "$PREFLIGHT" \
   && grep -q 'LiteLLM gateway' "$PREFLIGHT" \
   && grep -q 'ods-litellm' "$PREFLIGHT"; then
    pass "Host-native llama-server preflight checks LiteLLM gateway"
else
    fail "Host-native llama-server preflight must not require the in-stack ods-llama-server"
fi

# 12. Extension health checks honor .env port overrides
# An install can keep WHISPER_PORT=9100 (earlier AMD installs moved it there).
# Root preflight must follow that configured port instead of probing a stale
# hard-coded default.
if grep -q 'WHISPER_PORT_RESOLVED="${WHISPER_PORT:-9000}"' "$PREFLIGHT" \
   && grep -q 'TTS_PORT_RESOLVED="${TTS_PORT:-8880}"' "$PREFLIGHT" \
   && grep -q 'EMBEDDINGS_PORT_RESOLVED="${EMBEDDINGS_PORT:-8090}"' "$PREFLIGHT"; then
    pass "Extension health checks honor configured port overrides"
else
    fail "Extension health checks must honor WHISPER_PORT/TTS_PORT/EMBEDDINGS_PORT"
fi

if grep -q 'WHISPER_ENDPOINTS=("http://${SERVICE_HOST}:9000"' "$PREFLIGHT"; then
    fail "Whisper preflight still hard-codes host port 9000"
else
    pass "Whisper preflight no longer hard-codes host port 9000"
fi

# ── Runtime smoke test (no Docker required) ─────────────────────────────────

# 13. Script runs to completion without unbound variable or syntax errors
#     (Services won't be up, so we expect exit 1 — that is correct behavior)
set +e
err_output=$(
    SERVICE_HOST=localhost \
    OLLAMA_PORT="" \
    LLAMA_SERVER_PORT="" \
    GPU_BACKEND="" \
    bash "$PREFLIGHT" 2>&1
)
run_exit=$?
set -e

if echo "$err_output" | grep -qiE 'unbound variable|syntax error|command not found: \['; then
    fail "Script produced shell error: $(echo "$err_output" | grep -iE 'unbound variable|syntax error' | head -1)"
else
    pass "Script runs without shell errors (exit $run_exit is expected)"
fi

# 14. Exit code is 0 or 1; never an unexpected crash code
if [[ "$run_exit" -eq 0 ]] || [[ "$run_exit" -eq 1 ]]; then
    pass "Exit code is valid (0=pass, 1=fail): $run_exit"
else
    fail "Unexpected exit code $run_exit — script may have crashed"
fi

# ── detect_backend WSL2 hardware witness (no Docker required) ───────────────
#
# WSL2 passes the host GPU through a paravirtualized nvidia-smi bridge and
# exposes no matching /sys/class/drm card* vendor node. A sysfs-only probe
# returned "cpu", so the preflight printed a false
# "CPU mode — no GPU runtime required" pass and the NVIDIA branch that warns
# about a missing NVIDIA Docker runtime never ran. Drive the production
# functions against a mock sysfs tree and a stub nvidia-smi, the way
# tests/test_bootstrap_system_bash.py drives the bootstrap preliminary check.

BACKEND_DIR="$(mktemp -d)"
trap 'rm -rf "$BACKEND_DIR"' EXIT
mkdir -p "$BACKEND_DIR/drm/card0/device" "$BACKEND_DIR/bin"
SMI_CALLS="$BACKEND_DIR/smi-calls"

extract_fn() {
    awk -v fn="$1" 'index($0, fn "()") == 1 { inside = 1 } inside { print } inside && $0 == "}" { exit }' "$PREFLIGHT"
}

# Production functions verbatim; only the external sysfs path is redirected.
{
    printf '%s\n' 'set -euo pipefail'
    extract_fn is_wsl
    extract_fn detect_backend | sed "s#/sys/class/drm#$BACKEND_DIR/drm#g"
    printf '%s\n' 'detect_backend'
} > "$BACKEND_DIR/probe.sh"

# stub_nvidia_smi <shell body>
stub_nvidia_smi() {
    printf '#!/bin/bash\necho called >> "%s"\n%s\n' "$SMI_CALLS" "$1" > "$BACKEND_DIR/bin/nvidia-smi"
    chmod +x "$BACKEND_DIR/bin/nvidia-smi"
}

vendor() {
    mkdir -p "$BACKEND_DIR/drm/card0/device"
    printf '%s\n' "$1" > "$BACKEND_DIR/drm/card0/device/vendor"
}
clear_vendor() { rm -rf "$BACKEND_DIR/drm/card0"; }

printf '%s\n' 'Linux version 5.15.90.1-microsoft-standard-WSL2' > "$BACKEND_DIR/wsl-release"
printf '%s\n' 'Linux version 6.8.0-generic' > "$BACKEND_DIR/native-release"

# detect <wsl|native> <nvidia-smi body>
detect() {
    local out
    stub_nvidia_smi "$2"
    out=$(GPU_BACKEND="" ODS_PROC_VERSION_FILE="$BACKEND_DIR/$1-release" \
        SMI_CALLS="$SMI_CALLS" PATH="$BACKEND_DIR/bin:$PATH" \
        bash "$BACKEND_DIR/probe.sh" 2>/dev/null) || out=""
    printf '%s' "$out"
}

# 15. WSL2 without a DRM vendor node trusts a successful, non-empty query
clear_vendor
got=$(detect wsl 'printf "NVIDIA GeForce RTX 4070 SUPER\n"')
if [[ "$got" == "nvidia" ]]; then
    pass "WSL2 nvidia-smi query is the hardware witness"
else
    fail "WSL2 with a working GPU and no DRM vendor node reported '$got', expected nvidia"
fi

# 16. The query must succeed and name a GPU, not merely exit 0
for body in 'exit 1' 'exit 0'; do
    clear_vendor
    got=$(detect wsl "$body")
    if [[ "$got" == "cpu" ]]; then
        pass "WSL2 query '$body' does not prove a GPU"
    else
        fail "WSL2 query '$body' reported '$got', expected cpu"
    fi
done

# 17. Bare metal still requires the sysfs witness
clear_vendor
got=$(detect native 'printf "NVIDIA stub\n"')
if [[ "$got" == "cpu" ]]; then
    pass "Bare metal does not trust an installed nvidia-smi"
else
    fail "Bare metal without a DRM vendor node reported '$got', expected cpu"
fi

# 18. The bare-metal sysfs witness still selects NVIDIA
vendor 0x10de
got=$(detect native 'printf "NVIDIA GeForce RTX 4070 SUPER\n"')
if [[ "$got" == "nvidia" ]]; then
    pass "Bare metal 0x10de vendor node still selects nvidia"
else
    fail "Bare metal 0x10de vendor node reported '$got', expected nvidia"
fi

# 19. An explicit .env backend still wins over probing
clear_vendor
stub_nvidia_smi 'printf "NVIDIA GeForce RTX 4070 SUPER\n"'
got=$(GPU_BACKEND="amd" ODS_PROC_VERSION_FILE="$BACKEND_DIR/wsl-release" \
    SMI_CALLS="$SMI_CALLS" PATH="$BACKEND_DIR/bin:$PATH" \
    bash "$BACKEND_DIR/probe.sh" 2>/dev/null) || got=""
if [[ "$got" == "amd" ]]; then
    pass "An explicit .env backend still wins over probing"
else
    fail "Explicit GPU_BACKEND=amd reported '$got', expected amd"
fi

# ── Summary ────────────────────────────────────────────────────────────────

echo ""
echo "Result: $PASSED passed, $FAILED failed"
echo ""
[[ $FAILED -eq 0 ]]
