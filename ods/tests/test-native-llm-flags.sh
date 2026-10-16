#!/usr/bin/env bash
# The Windows Portal passes the host-native llama-server route with the
# --native-llm-* flags. Exercise install-core's real parser, the retired
# --lemonade-* shims (one release), the input validation, and phase 06's
# .env serialization of the route. All files live in a temporary directory.
# Variables below are consumed by evaluated installer code.
# shellcheck disable=SC2034
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CORE="$ROOT/install-core.sh"
PHASE="$ROOT/installers/phases/06-directories.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
source "$ROOT/lib/safe-env.sh"
source "$ROOT/lib/dotenv-quote.sh"
source "$ROOT/installers/lib/native-llm.sh"

pass=0
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
ok() { printf 'PASS: %s\n' "$*"; pass=$((pass + 1)); }

extract() {
    local text
    text="$(sed -n "$1" "$2")"
    [[ -n "$text" ]] || fail "installer block not found: $1"
    printf '%s\n' "$text"
}
defaults="$(extract '/^NATIVE_LLM_BASE_URL=""$/,/^_legacy_lemonade_api_key=""$/p' "$CORE")"
parser="$(extract '/^while \[\[ \$# -gt 0 \]\]; do$/,/^done$/p' "$CORE")"
shims="$(extract '/^if ((\${#_legacy_lemonade_flags\[@\]} > 0)); then$/,/^unset _legacy_lemonade_api_key$/p' "$CORE")"
mode_compat="$(extract '/^if \[\[ "\$ODS_MODE" == lemonade \]\]; then$/,/^fi$/p' "$CORE")"
native_checks="$(extract '/^if \[\[ -n "\$NATIVE_LLM_BASE_URL" \]\]; then$/,/^unset NATIVE_LLM_API_KEY_ENV$/p' "$CORE")"
vram_checks="$(extract '/^# Validate the native GPU VRAM/,/^unset _native_vram$/p' "$CORE")"

# parse ARGS...: run the installer's argument handling and print the route.
parse() (
    ODS_MODE="${ODS_MODE:-local}" ODS_MODE_EXPLICIT=false ENABLE_RECOMMENDED=false
    EXTERNAL_LLM_URL="" EXTERNAL_LLM_PROVIDER="" EXTERNAL_LLM_MODEL=""
    EXTERNAL_LLM_API_KEY_VALUE='' ODS_HOST_LLM_TRANSPORT="${ODS_HOST_LLM_TRANSPORT:-}"
    eval "$defaults"
    eval "$parser"
    eval "$shims"
    eval "$mode_compat"
    eval "$native_checks"
    eval "$vram_checks"
    printf '%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s\n' \
        "$NATIVE_LLM_BASE_URL" "$NATIVE_LLM_MODEL" "$NATIVE_LLM_LEGACY_MODEL_ID" "$NATIVE_LLM_CONTEXT_SIZE" \
        "$ODS_HOST_LLM_TRANSPORT" "$NATIVE_LLM_GPU_NAME" "$NATIVE_LLM_GPU_VRAM_MB" "$ODS_MODE" \
        "$ODS_MODE_EXPLICIT" "$EXTERNAL_LLM_URL" "$EXTERNAL_LLM_PROVIDER" "$EXTERNAL_LLM_MODEL"
)

rejects() {
    local label="$1" pattern="$2" rc=0
    shift 2
    parse "$@" >"$tmp/out" 2>"$tmp/err" || rc=$?
    [[ "$rc" -ne 0 ]] || fail "$label was accepted: $(cat "$tmp/out")"
    grep -q -- "$pattern" "$tmp/err" || fail "$label: expected '$pattern', got: $(cat "$tmp/err")"
    ok "$label is rejected"
}

# ── The native flags ─────────────────────────────────────────────────────────
got="$(parse --native-llm-url http://localhost:8080/v1 --native-llm-model Qwen3.6-35B-A3B-UD-Q4_K_M.gguf \
    --native-llm-context-size 131072 --native-llm-host-transport model-router \
    --native-llm-gpu-name 'AMD Radeon RX 9070 XT' --native-llm-gpu-vram-mb 016304)"
[[ "$got" == "http://localhost:8080|Qwen3.6-35B-A3B-UD-Q4_K_M.gguf||131072|model-router|AMD Radeon RX 9070 XT|16304|local|true|||" ]] \
    || fail "the native route flags were not parsed and normalized: $got"
ok "native route flags are parsed; the origin loses /v1 and VRAM is decimal"

got="$(parse --native-llm-url 'http://[::1]:18080/' --native-llm-model a.gguf)"
[[ "$got" == "http://[::1]:18080|a.gguf||||||local|true|||" ]] || fail "IPv6 loopback origin: $got"
ok "an IPv6 loopback origin is accepted"

rejects 'an https origin' 'must be an http://host:port origin' --native-llm-url https://localhost:8080 --native-llm-model a.gguf
rejects 'an origin with a query' 'must be an http://host:port origin' --native-llm-url 'http://localhost:8080?x=1' --native-llm-model a.gguf
rejects 'an origin without a port' 'must be an http://host:port origin' --native-llm-url http://localhost --native-llm-model a.gguf
rejects 'an origin with another path' 'must be an http://host:port origin' --native-llm-url http://localhost:8080/api --native-llm-model a.gguf
rejects 'a URL without a model' 'requires --native-llm-model' --native-llm-url http://localhost:8080
rejects 'a non-GGUF model' 'must be a GGUF file name' --native-llm-url http://localhost:8080 --native-llm-model model.bin
rejects 'a model path' 'must be a GGUF file name' --native-llm-url http://localhost:8080 --native-llm-model dir/a.gguf
rejects 'a hidden model file' 'must be a GGUF file name' --native-llm-url http://localhost:8080 --native-llm-model .a.gguf
rejects 'native flags without a URL' 'require --native-llm-url' --native-llm-model a.gguf
rejects 'an unknown transport' 'requires direct or model-router' --native-llm-host-transport proxy
rejects 'a missing transport value' 'requires direct or model-router' --native-llm-host-transport
rejects 'a missing URL value' 'requires an http://host:port origin' --native-llm-url --pixel

# The context Windows loaded the model with: whole tokens from 1024 (F39 rule).
for bad in 1023 0 -1 128k 1+1 0131072 '65536 ' 99999999999999999 'a[0]'; do
    rejects "context '$bad'" 'whole number of tokens from 1024' \
        --native-llm-url http://localhost:8080 --native-llm-model a.gguf --native-llm-context-size "$bad"
done
got="$(parse --native-llm-url http://localhost:8080 --native-llm-model a.gguf --native-llm-context-size 1024)"
[[ "$got" == *"|1024|"* ]] || fail "context 1024 must be accepted: $got"
ok "context accepts 1024 and up"

# The native server's key arrives through the environment, never argv.
export ODS_TEST_NATIVE_KEY=abababababababababababababababab
key_out="$(
    ODS_MODE=local ODS_MODE_EXPLICIT=false
    eval "$defaults"
    set -- --native-llm-url http://localhost:8080 --native-llm-model a.gguf --native-llm-api-key-env ODS_TEST_NATIVE_KEY
    eval "$parser"
    eval "$native_checks"
    [[ "$LLAMA_SERVER_API_KEY" == "$ODS_TEST_NATIVE_KEY" ]] && printf 'key-set'
    [[ -z "${NATIVE_LLM_API_KEY_ENV+x}" ]] && printf '|name-cleared'
)"
[[ "$key_out" == "key-set|name-cleared" ]] || fail "the native key must come from the named variable: $key_out"
ok "the native key is read from the named environment variable"
export ODS_TEST_NATIVE_KEY=not-hex
rejects 'a non-hex key' 'hex key of 32 to 512 digits' \
    --native-llm-url http://localhost:8080 --native-llm-model a.gguf --native-llm-api-key-env ODS_TEST_NATIVE_KEY
rejects 'an invalid key variable name' 'requires an environment variable name' --native-llm-api-key-env 'A-B'
export ODS_TEST_NATIVE_KEY=abababababababab
rejects 'a short key' 'hex key of 32 to 512 digits' \
    --native-llm-url http://localhost:8080 --native-llm-model a.gguf --native-llm-api-key-env ODS_TEST_NATIVE_KEY
unset ODS_TEST_NATIVE_KEY

for bad in '16GB' '-1' '1+1' '1.5' 'a[0]' '9223372036854775296' '999999999999999999999999999999'; do
    rejects "VRAM '$bad'" 'gpu-vram-mb' --native-llm-gpu-vram-mb "$bad"
done
got="$(parse --native-llm-gpu-vram-mb 08)"
[[ "$got" == *"|8|local|"* ]] || fail "an octal-looking VRAM must normalize to decimal: $got"
ok "VRAM normalizes leading zeros as decimal"

# ── Retired Lemonade flags (one release) ─────────────────────────────────────
got="$(parse --lemonade-url http://localhost:13305/api/v1 --lemonade-host-transport model-router \
    --lemonade-model extra.Qwen3.6-35B-A3B-UD-Q4_K_M.gguf --lemonade-context-size 32768 \
    --lemonade-gpu-name 'AMD Radeon(TM) 8060S Graphics' --lemonade-gpu-vram-mb 98304 2>"$tmp/notice")"
[[ "$got" == "http://localhost:13305|Qwen3.6-35B-A3B-UD-Q4_K_M.gguf||32768|model-router|AMD Radeon(TM) 8060S Graphics|98304|local|true|||" ]] \
    || fail "the Portal's Lemonade flags must map to the native route: $got"
grep -q 'NOTICE.*--native-llm-url' "$tmp/notice" || fail "the shim must name the replacement flags"
ok "the Portal's --lemonade-* flags map to the native route with a notice"

got="$(parse --lemonade-url http://localhost:13305 --lemonade-host-transport model-router \
    --lemonade-model Qwen3.6-35B-A3B-UD-Q4_K_M 2>/dev/null)"
[[ "$got" == "http://localhost:13305||Qwen3.6-35B-A3B-UD-Q4_K_M||model-router|||local|true|||" ]] \
    || fail "a Lemonade stem id must reach phase 02 for the catalog lookup: $got"
ok "a Lemonade stem id is kept for phase 02's catalog lookup"
rejects 'a Lemonade id with a path' 'is not a model id' \
    --lemonade-url http://localhost:13305 --lemonade-host-transport model-router --lemonade-model '../x'

got="$(parse --use-existing-lemonade --lemonade-url http://10.0.0.5:13305/api/v1 --lemonade-model Qwen3-0.6B-GGUF 2>"$tmp/notice")"
[[ "$got" == "|||||||local|true|http://10.0.0.5:13305|openai-compatible|Qwen3-0.6B-GGUF" ]] \
    || fail "the owner's own Lemonade must map to the generic external route: $got"
grep -q 'NOTICE.*--external-llm-url http://10.0.0.5:13305 --external-llm-provider openai-compatible' "$tmp/notice" \
    || fail "the external shim must name the replacement flags"
ok "--use-existing-lemonade maps to the generic OpenAI-compatible external route"
got="$(parse --use-existing-lemonade 2>/dev/null)"
[[ "$got" == *"|http://localhost:13305|openai-compatible|" ]] || fail "--use-existing-lemonade alone: $got"
ok "--use-existing-lemonade alone points at Lemonade's default port"
key_seen="$(
    ODS_MODE=local ODS_MODE_EXPLICIT=false EXTERNAL_LLM_URL='' EXTERNAL_LLM_MODEL='' EXTERNAL_LLM_API_KEY_VALUE=''
    eval "$defaults"
    set -- --use-existing-lemonade --lemonade-api-key sk-user-fixture
    eval "$parser"
    eval "$shims" 2>/dev/null
    [[ "$EXTERNAL_LLM_API_KEY_VALUE" == sk-user-fixture ]] && printf 'kept'
    bash -c '[[ -z "${EXTERNAL_LLM_API_KEY_VALUE:-}" ]]' && printf '|not-exported'
)"
[[ "$key_seen" == "kept|not-exported" ]] || fail "the user's Lemonade key must reach phase 06 without being exported: $key_seen"
ok "a Lemonade API key becomes the external key without entering child environments"

got="$(ODS_MODE=lemonade parse 2>"$tmp/notice")"
[[ "$got" == *"|local|"* ]] || fail "ODS_MODE=lemonade must read as local: $got"
grep -q 'ODS_MODE=lemonade is retired' "$tmp/notice" || fail "ODS_MODE=lemonade needs a notice"
ok "ODS_MODE=lemonade reads as local with a notice"

# ── The hardware scan shows the Windows GPU ──────────────────────────────────
card_source="$(extract '/# A host-native llama-server (Windows under WSL) runs the model on a GPU/,/^    fi$/p' \
    "$ROOT/installers/phases/02-detection.sh")"
card() (
    source "$ROOT/installers/lib/wsl-memory.sh"
    show_hardware_summary() { printf '%s|%s' "$1" "$2"; }
    GPU_NAME=None GPU_VRAM=0 CPU_INFO=cpu RAM_GB=47 DISK_AVAIL=896
    _ram_display='47.0 GiB' _host_ram_display='' RAM_IS_WSL=true
    eval "$card_source"
)
shown="$(NATIVE_LLM_BASE_URL=http://localhost:8080 NATIVE_LLM_GPU_NAME='AMD Radeon RX 9070 XT' NATIVE_LLM_GPU_VRAM_MB=16304 card)"
[[ "$shown" == "AMD Radeon RX 9070 XT (llama-server on Windows)|15.9 GiB (16304 MiB)" ]] || fail "hardware scan must show the Windows GPU: $shown"
shown="$(NATIVE_LLM_BASE_URL='' NATIVE_LLM_GPU_NAME='AMD Radeon RX 9070 XT' NATIVE_LLM_GPU_VRAM_MB=16304 card)"
[[ "$shown" == "None|0.0 GiB (0 MiB)" ]] || fail "without the native route the Linux probe is shown: $shown"
ok "the hardware scan shows the Windows GPU only for the native route"

fallback_source="$(extract '/# No GPU detected - fall back to CPU-only mode/,/^    return 1$/p' "$ROOT/installers/lib/detection.sh")"
probe() (
    ai() { printf 'AI:%s\n' "$1"; }
    warn() { printf 'WARN:%s\n' "$1"; }
    log() { printf 'LOG:%s\n' "$1"; }
    eval "fallback() { ${fallback_source}
}"
    fallback || true
    printf 'BACKEND:%s|VRAM:%s|COUNT:%s' "$GPU_BACKEND" "$GPU_VRAM" "$GPU_COUNT"
)
said="$(NATIVE_LLM_BASE_URL=http://localhost:8080 NATIVE_LLM_GPU_NAME='AMD Radeon RX 9070 XT' probe)"
[[ "$said" == "AI:"*"runs on AMD Radeon RX 9070 XT through llama-server on Windows"* \
   && "$said" != *"CPU-only mode"* && "$said" == *"BACKEND:cpu|VRAM:0|COUNT:0" ]] \
    || fail "the native route must replace the CPU-only warning: $said"
said="$(NATIVE_LLM_BASE_URL='' NATIVE_LLM_GPU_NAME='' probe)"
[[ "$said" == "WARN:No GPU detected."* && "$said" == *"LOG:CPU-only mode: llama.cpp will use CPU inference."* ]] \
    || fail "other hosts keep the CPU-only warning: $said"
said="$(NATIVE_LLM_BASE_URL=http://localhost:8080 NATIVE_LLM_GPU_NAME='' probe)"
[[ "$said" == "WARN:No GPU detected."* ]] || fail "a native route without a GPU name keeps the CPU diagnostics: $said"
ok "the native route replaces only the CPU-only warning"

# ── Phase 06 writes the route to .env ────────────────────────────────────────
route_block="$(extract '/^    ODS_WINDOWS_SYSTEM_DIRECTORY="\$(_env_get_explicit_first/,/^    ODS_HOST_LLM_TRANSPORT="\$ODS_HOST_LLM_TRANSPORT_VALUE"$/p' "$PHASE")"
template="$(extract '/^ODS_HOST_LLM_TRANSPORT=\$(dotenv_value/,/^\$(if \[\[ -n "\${ODS_WSL_STATE_ROOT}" \]\]/p' "$PHASE")"
for reader in _env_get _env_get_explicit_first; do
    definition="$(awk -v name="$reader" '
        $0 == "    " name "() {" { emit = 1 }
        emit { print }
        emit && $0 == "    }" { exit }
    ' "$PHASE")"
    [[ -n "$definition" ]] || fail "$reader not found in phase 06"
    eval "$definition"
done
error() { printf 'ERROR: %s\n' "$*" >&2; return 1; }

# route CASE EXPECTED_TRANSPORT: INHERITED/PERSISTED_* describe the run.
route() (
    local label="$1" expected="$2"
    unset ODS_HOST_LLM_TRANSPORT ODS_WINDOWS_SYSTEM_DIRECTORY ODS_WSL_STATE_ROOT LLAMA_SERVER_API_KEY
    NATIVE_LLM_BASE_URL=http://localhost:13305
    [[ -z "${INHERITED_TRANSPORT:-}" ]] || ODS_HOST_LLM_TRANSPORT="$INHERITED_TRANSPORT"
    [[ -z "${INHERITED_SYSTEM_DIRECTORY:-}" ]] || ODS_WINDOWS_SYSTEM_DIRECTORY="$INHERITED_SYSTEM_DIRECTORY"
    [[ -z "${INHERITED_STATE_ROOT:-}" ]] || ODS_WSL_STATE_ROOT="$INHERITED_STATE_ROOT"
    _env_existing=""
    if [[ -n "${PERSISTED_TRANSPORT:-}${PERSISTED_SYSTEM_DIRECTORY:-}${PERSISTED_STATE_ROOT:-}" ]]; then
        _env_existing="$tmp/existing.env"
        : > "$_env_existing"
        [[ -z "${PERSISTED_TRANSPORT:-}" ]] || printf 'ODS_HOST_LLM_TRANSPORT=%s\n' "$PERSISTED_TRANSPORT" >> "$_env_existing"
        [[ -z "${PERSISTED_SYSTEM_DIRECTORY:-}" ]] \
            || printf 'ODS_WINDOWS_SYSTEM_DIRECTORY=%s\n' "$(dotenv_value "$PERSISTED_SYSTEM_DIRECTORY")" >> "$_env_existing"
        [[ -z "${PERSISTED_STATE_ROOT:-}" ]] \
            || printf 'ODS_WSL_STATE_ROOT=%s\n' "$(dotenv_value "$PERSISTED_STATE_ROOT")" >> "$_env_existing"
    fi
    eval "$route_block"
    eval 'cat > "$tmp/route.env" << ENV_EOF
'"$template"'
ENV_EOF'
    unset ODS_HOST_LLM_TRANSPORT NATIVE_LLM_BASE_URL NATIVE_LLM_CONTAINER_BASE_URL
    unset ODS_WINDOWS_SYSTEM_DIRECTORY ODS_WSL_STATE_ROOT
    load_env_file "$tmp/route.env"
    [[ "$ODS_HOST_LLM_TRANSPORT" == "$expected" ]] || fail "$label: transport $ODS_HOST_LLM_TRANSPORT, expected $expected"
    [[ "$NATIVE_LLM_BASE_URL" == http://localhost:13305 \
       && "$NATIVE_LLM_CONTAINER_BASE_URL" == http://host.docker.internal:13305 ]] \
        || fail "$label: origins $NATIVE_LLM_BASE_URL / $NATIVE_LLM_CONTAINER_BASE_URL"
    [[ "${ODS_WINDOWS_SYSTEM_DIRECTORY:-}" == "${EXPECTED_SYSTEM_DIRECTORY:-}" ]] \
        || fail "$label: system directory '${ODS_WINDOWS_SYSTEM_DIRECTORY:-}'"
    [[ "${ODS_WSL_STATE_ROOT:-}" == "${EXPECTED_STATE_ROOT:-}" ]] || fail "$label: state root '${ODS_WSL_STATE_ROOT:-}'"
    if [[ -z "${EXPECTED_SYSTEM_DIRECTORY:-}" ]]; then
        ! grep -q '^ODS_WINDOWS_SYSTEM_DIRECTORY=' "$tmp/route.env" || fail "$label wrote an empty system directory"
    fi
    printf 'PASS: %s\n' "$label"
)
route 'Omitted transport defaults to direct' direct
INHERITED_TRANSPORT=model-router route 'Explicit model-router transport' model-router
PERSISTED_TRANSPORT=model-router route 'A rerun keeps the saved model-router transport' model-router
INHERITED_TRANSPORT=direct PERSISTED_TRANSPORT=model-router route 'An explicit transport overrides the saved one' direct
INHERITED_SYSTEM_DIRECTORY='D:\Operating $ystem\System32' EXPECTED_SYSTEM_DIRECTORY='D:\Operating $ystem\System32' \
    route 'The Windows system directory is kept literally' direct
PERSISTED_TRANSPORT=model-router PERSISTED_SYSTEM_DIRECTORY='E:\Windows\System32' EXPECTED_SYSTEM_DIRECTORY='E:\Windows\System32' \
    route 'A rerun keeps the saved Windows system directory' model-router
INHERITED_STATE_ROOT="D:\ODS state\it's [private] \$(literal)" EXPECTED_STATE_ROOT="D:\ODS state\it's [private] \$(literal)" \
    route 'The Windows state location survives dotenv quoting' direct
INHERITED_STATE_ROOT='D:\ODS state\selected' PERSISTED_STATE_ROOT='E:\ODS state\saved' EXPECTED_STATE_ROOT='D:\ODS state\selected' \
    route 'The Windows selection wins over the saved state location' direct
rc=0
( INHERITED_TRANSPORT=proxy route 'An invalid transport' direct ) >"$tmp/out" 2>&1 || rc=$?
[[ "$rc" -ne 0 ]] && grep -q 'ODS_HOST_LLM_TRANSPORT must be direct or model-router' "$tmp/out" \
    || fail "an invalid transport must be rejected before writing the route"
ok "an invalid transport is rejected before writing the route"

# The schema's transport values match.
jq -e '.properties.ODS_HOST_LLM_TRANSPORT.enum | sort == ["direct", "model-router"]' "$ROOT/.env.schema.json" >/dev/null \
    || fail "ODS_HOST_LLM_TRANSPORT must allow direct and model-router"
ok "the schema documents both transports"

printf 'native LLM flags: all checks passed\n'
