#!/usr/bin/env bash
# Phase 02 records the model a host-native llama-server (the Windows Portal)
# serves from its catalog entry, repairs only the mismatch earlier installs
# wrote, and stops on any other difference. Ported from fix F39's external
# Lemonade phase test to --native-llm-model / --native-llm-context-size.
# Runs the real Phase 02 block with the real helper and catalog.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE="$ROOT_DIR/installers/phases/02-detection.sh"
TMP_DIR="$(mktemp -d -t ods-native-phase02-XXXXXX)"
trap 'rm -rf "$TMP_DIR"' EXIT
FAILURES=0

pass() { echo "[PASS] $*"; }
fail() { echo "[FAIL] $*" >&2; FAILURES=$((FAILURES + 1)); }

SERVED="Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
SERVED_MODEL="qwen3.6-35b-a3b"

# From the served-model projection through active-model preservation.
grep -q '^# Host-native llama-server: llama-server on the Windows host serves' "$PHASE" \
    || { echo "[FAIL] Phase 02 host-native block not found" >&2; exit 1; }
awk '/^# Host-native llama-server: llama-server on the Windows host serves/{p=1}
     /^# Display hardware summary/{p=0} p' "$PHASE" > "$TMP_DIR/phase-block.sh"

# Phase 02 state on a Strix Halo Windows host: WSL sees no GPU, so the
# catalog selector picked a CPU model before this block runs.
cat > "$TMP_DIR/prefix.sh" <<EOF
set -euo pipefail
SCRIPT_DIR="$ROOT_DIR"
INSTALL_DIR="\$1"
LOG_FILE="\$1/phase.log"
source "\$SCRIPT_DIR/lib/safe-env.sh"
source "\$SCRIPT_DIR/installers/lib/external-services.sh"
source "\$SCRIPT_DIR/installers/lib/native-llm.sh"
LLM_MODEL=qwen3.5-9b
GGUF_FILE=Qwen3.5-9B-Q4_K_M.gguf
MAX_CONTEXT=65536
MODEL_RUNTIME_PROFILE=cpu-64k-q8-kv
LLAMA_ARG_CACHE_TYPE_K=q8_0
GPU_BACKEND=cpu
TIER=4
NATIVE_LLM_BASE_URL=http://127.0.0.1:8080
NATIVE_LLM_MODEL=$SERVED
NATIVE_LLM_CONTEXT_SIZE=131072
NATIVE_LLM_GPU_NAME="AMD Radeon(TM) 8060S Graphics"
ODS_MODE_EXPLICIT=false
ODS_RESELECT_MODEL=false
_selector_python=python3
log() { printf '%s\n' "\$*" >> "\$LOG_FILE"; }
ai_warn() { printf 'WARN %s\n' "\$*" >&2; }
error() { printf 'ERROR %s\n' "\$*" >&2; }
EOF
# shellcheck disable=SC2016  # expanded by the generated script
printf '%s\n' 'printf "%s|%s|%s|%s|%s|%s|%s" "$LLM_MODEL" "$GGUF_FILE" "$MAX_CONTEXT" "$MODEL_SELECTION_SOURCE" "$INSTALLER_RECOMMENDED_GGUF" "${LLAMA_ARG_CACHE_TYPE_K:-unset}" "$NATIVE_LLM_MODEL"' \
    > "$TMP_DIR/report.sh"

# run_block NAME [assignments...]: run prefix, overrides, the block and the
# report; leaves $TMP_DIR/NAME.{out,err,rc}. The install dir is $TMP_DIR/NAME.
run_block() {
    local name="$1" rc=0
    shift
    mkdir -p "$TMP_DIR/$name"
    {
        cat "$TMP_DIR/prefix.sh"
        printf '%s\n' "$@"
        cat "$TMP_DIR/phase-block.sh" "$TMP_DIR/report.sh"
    } > "$TMP_DIR/$name.sh"
    bash "$TMP_DIR/$name.sh" "$TMP_DIR/$name" >"$TMP_DIR/$name.out" 2>"$TMP_DIR/$name.err" || rc=$?
    printf '%s\n' "$rc" > "$TMP_DIR/$name.rc"
}

# The .env an earlier fresh Windows-AMD install wrote: the Linux host's own
# CPU pick saved next to the model Windows serves.
write_fresh_install_mismatch() {
    cat > "$1/.env" <<'EOF'
ODS_MODE=local
LLM_BACKEND=llama-server
NATIVE_LLM_BASE_URL=http://127.0.0.1:8080
EXTERNAL_LLM_URL=
LLM_MODEL=qwen3.5-9b
GGUF_FILE=Qwen3.5-9B-Q4_K_M.gguf
MAX_CONTEXT=65536
MODEL_SELECTION_SOURCE=installer
ODS_ACTIVE_MODEL_STORE=default
MODEL_RECOMMENDED_MODEL=qwen3.5-9b
MODEL_RECOMMENDED_GGUF=Qwen3.5-9B-Q4_K_M.gguf
MODEL_RUNTIME_PROFILE='cpu-64k-q8-kv'
LLAMA_ARG_CACHE_TYPE_K=q8_0
EOF
}

served="$SERVED_MODEL|$SERVED|131072|installer|$SERVED|unset|$SERVED"

# 1. A fresh install records the served model, not the Linux host's pick.
run_block fresh
if [[ "$(cat "$TMP_DIR/fresh.rc")" == 0 && "$(cat "$TMP_DIR/fresh.out")" == "$served" ]]; then
    pass "fresh install records the served catalog model at the loaded context"
else
    fail "fresh install must record the served model: $(cat "$TMP_DIR/fresh.out") / $(cat "$TMP_DIR/fresh.err")"
fi

# 2. The retired --lemonade-model id still names the served file (one release).
run_block legacy 'NATIVE_LLM_MODEL=' 'NATIVE_LLM_LEGACY_MODEL_ID=extra.Qwen3.6-35B-A3B-UD-Q4_K_M.gguf'
if [[ "$(cat "$TMP_DIR/legacy.rc")" == 0 && "$(cat "$TMP_DIR/legacy.out")" == "$served" ]]; then
    pass "a retired Lemonade model id resolves to the served GGUF"
else
    fail "a retired Lemonade model id must resolve to the served GGUF: $(cat "$TMP_DIR/legacy.out")"
fi

# 3. A model outside the catalog is not recorded as something else.
run_block unknown 'NATIVE_LLM_MODEL=Not-A-Catalog-Model.gguf'
if [[ "$(cat "$TMP_DIR/unknown.rc")" != 0 && ! -s "$TMP_DIR/unknown.out" ]] \
    && grep -q 'is not in the ODS model catalog' "$TMP_DIR/unknown.err"; then
    pass "a served model outside the catalog stops the install"
else
    fail "a served model outside the catalog must stop the install"
fi

# 4. A rerun over the mismatch an earlier fresh install wrote is repaired, and says so.
mkdir -p "$TMP_DIR/repair"
write_fresh_install_mismatch "$TMP_DIR/repair"
run_block repair
if [[ "$(cat "$TMP_DIR/repair.rc")" == 0 && "$(cat "$TMP_DIR/repair.out")" == "$served" ]] \
    && grep -q '^WARN Corrected the saved model details' "$TMP_DIR/repair.err" \
    && grep -qF "LLM_MODEL qwen3.5-9b -> $SERVED_MODEL" "$TMP_DIR/repair/phase.log"; then
    pass "the installer-written mismatch is repaired from the served model"
else
    fail "the installer-written mismatch must be repaired: $(cat "$TMP_DIR/repair.out") / $(cat "$TMP_DIR/repair.err")"
fi

# 5. An operator's own choice is never rewritten: the rerun stops.
mkdir -p "$TMP_DIR/operator"
write_fresh_install_mismatch "$TMP_DIR/operator"
sed -i 's/^MODEL_SELECTION_SOURCE=installer$/MODEL_SELECTION_SOURCE=operator/' "$TMP_DIR/operator/.env"
run_block operator
if [[ "$(cat "$TMP_DIR/operator.rc")" != 0 && ! -s "$TMP_DIR/operator.out" ]] \
    && grep -q 'was not written by the installer' "$TMP_DIR/operator.err"; then
    pass "an operator's different selection stops the rerun"
else
    fail "an operator's different selection must stop the rerun"
fi

# 6. A consistent rerun keeps the Dashboard's context and ownership unless
#    Windows reports the context it loaded.
mkdir -p "$TMP_DIR/kept"
cat > "$TMP_DIR/kept/.env" <<EOF
ODS_MODE=local
LLM_BACKEND=llama-server
NATIVE_LLM_BASE_URL=http://127.0.0.1:8080
EXTERNAL_LLM_URL=
LLM_MODEL=$SERVED_MODEL
GGUF_FILE=$SERVED
MAX_CONTEXT=65536
MODEL_SELECTION_SOURCE=dashboard
EOF
run_block kept 'NATIVE_LLM_CONTEXT_SIZE='
cp "$TMP_DIR/kept/.env" "$TMP_DIR/kept.env"
mkdir -p "$TMP_DIR/loaded"
cp "$TMP_DIR/kept.env" "$TMP_DIR/loaded/.env"
run_block loaded
if [[ "$(cat "$TMP_DIR/kept.rc")" == 0 \
        && "$(cut -d'|' -f3,4 "$TMP_DIR/kept.out")" == "65536|dashboard" \
        && "$(cut -d'|' -f3,4 "$TMP_DIR/loaded.out")" == "131072|dashboard" ]]; then
    pass "a consistent rerun keeps the owner, and the context Windows loaded wins"
else
    fail "a consistent rerun must keep the owner and context: $(cat "$TMP_DIR/kept.out") / $(cat "$TMP_DIR/loaded.out")"
fi

# 7. Without the native flags a retained host-native install is not switched
#    to a model in this Linux environment.
mkdir -p "$TMP_DIR/flagless"
cp "$TMP_DIR/kept.env" "$TMP_DIR/flagless/.env"
run_block flagless 'NATIVE_LLM_BASE_URL=' 'NATIVE_LLM_MODEL=' 'NATIVE_LLM_CONTEXT_SIZE='
if [[ "$(cat "$TMP_DIR/flagless.rc")" != 0 ]] \
    && grep -q 'uses a llama-server that Windows setup manages' "$TMP_DIR/flagless.err"; then
    pass "a rerun without the native flags stops instead of switching models"
else
    fail "a rerun without the native flags must stop"
fi

# 8. Selecting an API for this run is an explicit switch away from the
#    Windows llama-server (fleet, Strixy: install.ps1 -ExternalLlmUrl on a
#    native install was refused with advice to pass native flags).
mkdir -p "$TMP_DIR/api-switch"
cp "$TMP_DIR/kept.env" "$TMP_DIR/api-switch/.env"
run_block api-switch 'NATIVE_LLM_BASE_URL=' 'NATIVE_LLM_MODEL=' 'NATIVE_LLM_CONTEXT_SIZE=' \
    'EXTERNAL_LLM_URL=https://api.example.test'
if ! grep -q 'uses a llama-server that Windows setup manages' "$TMP_DIR/api-switch.err"; then
    pass "an API selected for this run switches away from the Windows llama-server"
else
    fail "an API selected for this run must not be refused as a native rerun: $(cat "$TMP_DIR/api-switch.err")"
fi

if [[ "$FAILURES" -gt 0 ]]; then
    echo "$FAILURES check(s) failed" >&2
    exit 1
fi
echo "All host-native Phase 02 checks passed"
