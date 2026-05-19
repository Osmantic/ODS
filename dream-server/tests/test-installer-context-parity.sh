#!/usr/bin/env bash
# ============================================================================
# Dream Server installer context parity tests
# ============================================================================
# Locks the model-context defaults that let Hermes work during first-run
# bootstrap and after full model upgrade across Linux, macOS, and Windows.
# ============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PASS=0

pass() {
    echo "  PASS: $1"
    PASS=$((PASS + 1))
}

fail() {
    echo "  FAIL: $1" >&2
    exit 1
}

assert_grep() {
    local file="$1"
    local pattern="$2"
    local label="$3"

    [[ -f "$file" ]] || fail "missing $file"
    if grep -Eq -- "$pattern" "$file"; then
        pass "$label"
    else
        fail "$label"
    fi
}

echo "=== Installer context parity ==="

echo ""
echo "Bootstrap context floor:"
assert_grep "installers/lib/bootstrap-model.sh" '^BOOTSTRAP_MAX_CONTEXT=65536$' \
    "Linux bootstrap context is 64K"
assert_grep "installers/macos/lib/tier-map.sh" '^BOOTSTRAP_MAX_CONTEXT=65536$' \
    "macOS bootstrap context is 64K"
assert_grep "installers/windows/lib/tier-map.ps1" 'BOOTSTRAP_MAX_CONTEXT[[:space:]]*=[[:space:]]*65536' \
    "Windows bootstrap context is 64K"

echo ""
echo "Hermes target context:"
assert_grep "installers/phases/03-features.sh" 'HERMES_CONTEXT_SIZE=.*131072' \
    "Linux Hermes target context is 128K"
assert_grep "installers/macos/install-macos.sh" '^HERMES_CONTEXT_SIZE=131072$' \
    "macOS Hermes target context is 128K"
assert_grep "installers/windows/phases/03-features.ps1" 'hermesContextSize[[:space:]]*=[[:space:]]*131072' \
    "Windows Hermes target context is 128K"

echo ""
echo ".env context parity:"
assert_grep "installers/phases/06-directories.sh" '^MAX_CONTEXT=\$\{MAX_CONTEXT\}$' \
    "Linux .env generator writes MAX_CONTEXT"
assert_grep "installers/phases/06-directories.sh" '^CTX_SIZE=\$\{MAX_CONTEXT\}$' \
    "Linux .env generator writes CTX_SIZE from MAX_CONTEXT"
assert_grep "installers/macos/lib/env-generator.sh" '^MAX_CONTEXT=\$\{MAX_CONTEXT\}$' \
    "macOS .env generator writes MAX_CONTEXT"
assert_grep "installers/macos/lib/env-generator.sh" '^CTX_SIZE=\$\{MAX_CONTEXT\}$' \
    "macOS .env generator writes CTX_SIZE from MAX_CONTEXT"
assert_grep "installers/windows/lib/env-generator.ps1" '^MAX_CONTEXT=\$\(\$TierConfig\.MaxContext\)' \
    "Windows .env generator writes MAX_CONTEXT"
assert_grep "installers/windows/lib/env-generator.ps1" '^CTX_SIZE=\$\(\$TierConfig\.MaxContext\)' \
    "Windows .env generator writes CTX_SIZE from MaxContext"

echo ""
echo "Bootstrap context rewrites:"
assert_grep "installers/phases/11-services.sh" '"MAX_CONTEXT=\$MAX_CONTEXT"[[:space:]]+"CTX_SIZE=\$MAX_CONTEXT"' \
    "Linux bootstrap rewrite updates MAX_CONTEXT and CTX_SIZE together"
assert_grep "installers/macos/install-macos.sh" 'MAX_CONTEXT=.*MAX_CONTEXT' \
    "macOS bootstrap rewrite updates MAX_CONTEXT"
assert_grep "installers/macos/install-macos.sh" 'CTX_SIZE=.*MAX_CONTEXT' \
    "macOS bootstrap rewrite updates CTX_SIZE"
assert_grep "installers/windows/install-windows.ps1" 'MAX_CONTEXT=\$\(\$tierConfig\.MaxContext\)' \
    "Windows bootstrap rewrite updates MAX_CONTEXT"
assert_grep "installers/windows/install-windows.ps1" 'CTX_SIZE=\$\(\$tierConfig\.MaxContext\)' \
    "Windows bootstrap rewrite updates CTX_SIZE"

echo ""
echo "Hermes config patch paths:"
assert_grep "installers/phases/11-services.sh" '_hermes_context="\$\{MAX_CONTEXT:-131072\}"' \
    "Linux Hermes patcher uses selected context"
assert_grep "installers/phases/11-services.sh" '--context-length "\$_hermes_context"' \
    "Linux Hermes patcher receives context length"
assert_grep "installers/macos/install-macos.sh" '--context-length "\$MAX_CONTEXT"' \
    "macOS Hermes patcher receives context length"
assert_grep "installers/windows/install-windows.ps1" 'Update-HermesConfigFile.*ContextLength \(\[int\]\$tierConfig\.MaxContext\)' \
    "Windows Hermes patcher receives context length"

echo ""
echo "Hermes config patcher behavior:"
python_cmd="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)"
[[ -n "$python_cmd" ]] || fail "python is required to test Hermes config patcher"

tmp_hermes="$(mktemp)"
trap 'rm -f "$tmp_hermes"' EXIT
cat > "$tmp_hermes" <<'HERMES_EOF'
model:
  default: "old-model"
  provider: "custom"
  base_url: "http://old.example/v1"
  context_length: 8192

compression:
  threshold: 10000
  target_ratio: 0.5
terminal:
  backend: "local"
HERMES_EOF

"$python_cmd" scripts/patch-hermes-config.py "$tmp_hermes" \
    --model "Qwen3.5-2B-Q4_K_M.gguf" \
    --base-url "http://llama-server:8080/v1" \
    --context-length 65536 >/dev/null

grep -q 'default: "Qwen3.5-2B-Q4_K_M.gguf"' "$tmp_hermes" \
    || fail "Hermes patcher updates model.default"
pass "Hermes patcher updates model.default"
grep -q 'base_url: "http://llama-server:8080/v1"' "$tmp_hermes" \
    || fail "Hermes patcher updates base_url"
pass "Hermes patcher updates base_url"
grep -q '^  context_length: 65536$' "$tmp_hermes" \
    || fail "Hermes patcher updates model.context_length"
pass "Hermes patcher updates model.context_length"
grep -q '^    context_length: 65536$' "$tmp_hermes" \
    || fail "Hermes patcher writes auxiliary compression context"
pass "Hermes patcher writes auxiliary compression context"
grep -q '^  threshold: 0.50$' "$tmp_hermes" \
    || fail "Hermes patcher normalizes compression threshold"
pass "Hermes patcher normalizes compression threshold"
grep -q '^  target_ratio: 0.20$' "$tmp_hermes" \
    || fail "Hermes patcher normalizes compression target_ratio"
pass "Hermes patcher normalizes compression target_ratio"
grep -q '^  protect_last_n: 20$' "$tmp_hermes" \
    || fail "Hermes patcher writes protect_last_n"
pass "Hermes patcher writes protect_last_n"

echo ""
echo "Results: $PASS passed"
