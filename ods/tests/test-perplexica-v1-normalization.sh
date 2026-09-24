#!/usr/bin/env bash
# Regression: every installer path that appends /v1 to a configured LLM base
# URL must normalize trailing slashes first. A value like "http://host/v1/"
# otherwise becomes "http://host/v1/v1" and Perplexica's model calls 404.
#
# Covers the two sites not already guarded by test_perplexica_repair_python.py:
#   - installers/phases/12-health.sh (Linux installer, PERPLEXICA_LLM_BASE_URL)
#   - installers/macos/lib/env-generator.sh configure_perplexica (macOS)
#
# Usage: ./tests/test-perplexica-v1-normalization.sh

set -euo pipefail

if (( BASH_VERSINFO[0] < 4 )); then
    for modern_bash in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        if [[ -x "$modern_bash" ]]; then
            exec "$modern_bash" "$0" "$@"
        fi
    done
    printf '[SKIP] test requires Bash 4+\n'
    exit 0
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root_dir="$(cd "$script_dir/.." && pwd)"
health_phase="$root_dir/installers/phases/12-health.sh"
env_gen="$root_dir/installers/macos/lib/env-generator.sh"

PASSED=0
FAILED=0
pass() { printf '  [PASS] %s\n' "$1"; PASSED=$((PASSED + 1)); }
fail() { printf '  [FAIL] %s\n' "$1"; FAILED=$((FAILED + 1)); }

# ---------------------------------------------------------------------------
# Phase 12 block: extract the real normalization+suffix region and eval it.
# The slice runs the shipped code verbatim, not a reimplementation.
# ---------------------------------------------------------------------------
phase12_slice="$(sed -n '/PERPLEXICA_LLM_BASE_URL="${LLM_API_URL/,/^    esac/p' "$health_phase")"
if [[ -z "$phase12_slice" ]]; then
    fail "could not extract PERPLEXICA_LLM_BASE_URL block from 12-health.sh"
else
    run_phase12() {
        local base_url="$1" result
        result="$(
            LLM_API_URL="$base_url" _perplexica_switchboard_mode=disabled \
            PERPLEXICA_LLM_BASE_URL= PERPLEXICA_MODEL= \
            bash -c "$phase12_slice; printf '%s' \"\$PERPLEXICA_LLM_BASE_URL\""
        )"
        printf '%s' "$result"
    }

    check_phase12() {
        local input="$1" want="$2"
        local got
        got="$(run_phase12 "$input")"
        if [[ "$got" == "$want" ]]; then
            pass "12-health: $input -> $want"
        else
            fail "12-health: $input -> $got (want $want)"
        fi
    }

    check_phase12 "http://llama-server:8080"      "http://llama-server:8080/v1"
    check_phase12 "http://llama-server:8080/"     "http://llama-server:8080/v1"
    check_phase12 "http://host:8080/v1"           "http://host:8080/v1"
    check_phase12 "http://host:8080/v1/"          "http://host:8080/v1"
    check_phase12 "http://host:8080/v1//"         "http://host:8080/v1"
    check_phase12 "http://host:8080/api/v1"       "http://host:8080/api/v1"
    check_phase12 "http://host:8080/api/v1/"      "http://host:8080/api/v1"
fi

# ---------------------------------------------------------------------------
# macOS env-generator: source the library, stub python3 to capture the env
# vars configure_perplexica exports to the config writer.
# ---------------------------------------------------------------------------
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT
bin_dir="$tmp_dir/bin"
capture="$tmp_dir/perplexica.env"
mkdir -p "$bin_dir"

cat > "$bin_dir/python3" <<'PYSTUB'
#!/usr/bin/env bash
# Probes from lib/python-cmd.sh use -c; answer them without touching stdin.
if [[ "${1:-}" == "-c" ]]; then
    exit 0
fi
# The config writer is invoked as `python3 - <<'PY'`; drain the heredoc and
# record the exported PERPLEXICA_* env vars.
if [[ "${1:-}" == "-" ]]; then
    cat >/dev/null
    env | grep '^PERPLEXICA' > "${ODS_TEST_CAPTURE:?}"
    exit 0
fi
exit 0
PYSTUB
cat > "$bin_dir/python" <<'PYSTUB'
#!/usr/bin/env bash
exec "$(command -v python3)" "$@"
PYSTUB
chmod +x "$bin_dir/python3" "$bin_dir/python"

check_env_gen() {
    local input="$1" want="$2"
    : > "$capture"
    (
        export PATH="$bin_dir:$PATH"
        export ODS_TEST_CAPTURE="$capture"
        # shellcheck disable=SC1090
        . "$env_gen"
        configure_perplexica 3004 test-model "$input" test-key >/dev/null 2>&1 || true
    )
    local got
    got="$(grep '^PERPLEXICA_LLM_BASE_URL=' "$capture" | cut -d= -f2-)"
    if [[ "$got" == "$want" ]]; then
        pass "env-generator: $input -> $want"
    else
        fail "env-generator: $input -> $got (want $want)"
    fi
}

check_env_gen "http://host.docker.internal:8080"   "http://host.docker.internal:8080/v1"
check_env_gen "http://host.docker.internal:8080/"  "http://host.docker.internal:8080/v1"
check_env_gen "http://host:8080/v1"                "http://host:8080/v1"
check_env_gen "http://host:8080/v1/"               "http://host:8080/v1"
check_env_gen "http://host:8080/v1//"              "http://host:8080/v1"
check_env_gen "http://host:8080/api/v1/"           "http://host:8080/api/v1"

echo ""
echo "Result: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]]
