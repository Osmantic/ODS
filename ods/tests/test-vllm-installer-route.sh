#!/usr/bin/env bash
# The Linux installer's model server choice (llama.cpp or the vLLM engine).
#
#   - install-core parses --llm-backend vllm|llama-server;
#   - installers/lib/llm-backend.sh names every route that cannot run vLLM;
#   - phase 02's real decision, with the real selector and catalog and a fake
#     nvidia-smi: a vLLM model that fits picks vLLM, nothing fitting keeps
#     llama.cpp untouched, --llm-backend vllm stops when nothing fits,
#     --llm-backend llama-server never looks, WSL, macOS, AMD, cloud, hybrid,
#     external, host-native and gateway-only routes never use vLLM, a rerun of
#     a llama.cpp install keeps llama.cpp, and every selector failure other
#     than "nothing fits" stops the install;
#   - a rerun of a vLLM install keeps the vLLM engine and its installed model
#     (scripts/preserve-active-model.py's contract, faked here) and stops,
#     never falling back to llama.cpp, when the model cannot be kept, vLLM
#     cannot run (cloud mode included) or nothing fits; only --llm-backend
#     llama-server or --reselect-model chooses again;
#   - the vLLM pick replaces the tier map's GGUF and loads the VLLM_* keys;
#   - exporting this run's LLM_BACKEND leaves every other resolver selection
#     byte-identical, and the fast-start bootstrap GGUF is never used on vLLM.
# Needs no Docker daemon and no GPU.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CORE="$ROOT_DIR/install-core.sh"
PHASE02="$ROOT_DIR/installers/phases/02-detection.sh"
PASS=0
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
pass() { printf '[PASS] %s\n' "$*"; PASS=$((PASS + 1)); }
# ODS_TEST_SECTION=N runs only section N (a quick rerun while editing).
only() { [[ -z "${ODS_TEST_SECTION:-}" || "$ODS_TEST_SECTION" == "$1" ]]; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
# shellcheck source=../installers/lib/llm-backend.sh
source "$ROOT_DIR/installers/lib/llm-backend.sh"
pinned="$(ods_vllm_image_from_lock "$ROOT_DIR/config/dependency-lock.json")"

extract() {
    local text
    text="$(sed -n "$1" "$2")"
    [[ -n "$text" ]] || fail "installer block not found: $1 in $2"
    printf '%s\n' "$text"
}

# ── 1. install-core parses --llm-backend ───────────────────────────────────
if only 1; then
parser="$(extract '/^while \[\[ \$# -gt 0 \]\]; do$/,/^done$/p' "$CORE")"
grep -q '^LLM_BACKEND_REQUESTED=""$' "$CORE" || fail "install-core must default to the automatic choice"
parse() (
    LLM_BACKEND_REQUESTED=""
    eval "$parser"
    printf '%s\n' "$LLM_BACKEND_REQUESTED"
)
[[ "$(parse --llm-backend vllm)" == vllm ]] || fail "--llm-backend vllm was not parsed"
[[ "$(parse --llm-backend llama-server --non-interactive)" == llama-server ]] \
    || fail "--llm-backend llama-server was not parsed"
[[ "$(parse --non-interactive)" == "" ]] || fail "no flag must leave the choice to phase 02"
for bad in auto lemonade '' --pixel; do
    rc=0
    parse --llm-backend "$bad" >"$tmp/out" 2>"$tmp/err" || rc=$?
    [[ "$rc" -ne 0 ]] && grep -q -- '--llm-backend requires vllm or llama-server' "$tmp/err" \
        || fail "--llm-backend '$bad' must be refused (rc=$rc): $(cat "$tmp/out" "$tmp/err")"
done
grep -qF -- '--llm-backend vllm|llama-server' "$CORE" || fail "install-core --help must document --llm-backend"
pass "install-core parses --llm-backend vllm|llama-server and refuses anything else"

macos_parser="$(extract '/^while \[\[ \$# -gt 0 \]\]; do$/,/^done$/p' "$ROOT_DIR/installers/macos/install-macos.sh")"
macos_parse() ( eval "$macos_parser"; printf 'parsed\n' )
[[ "$(macos_parse --llm-backend llama-server)" == parsed ]] || fail "macOS must accept --llm-backend llama-server"
rc=0
macos_parse --llm-backend vllm >"$tmp/out" 2>"$tmp/err" || rc=$?
[[ "$rc" -ne 0 ]] && grep -q 'needs Linux with an NVIDIA GPU;' "$tmp/err" && grep -q 'discord.gg' "$tmp/err" \
    || fail "macOS must refuse --llm-backend vllm with the reason and the Discord link"
pass "the macOS installer accepts llama-server and refuses vllm with a reason"

# The command shown after the one-time Secure Boot key reboot resumes with
# the same --llm-backend choice.
resume_block="$(extract '/^    local resume_args=""$/,/^    local resume_command=/p' "$ROOT_DIR/installers/lib/detection.sh")"
resume_command() (
    ENABLE_VOICE=false ENABLE_WORKFLOWS=false ENABLE_RAG=false ENABLE_HERMES=false TIER="" OFFLINE_MODE=false
    ODS_RESELECT_MODEL=false SCRIPT_DIR=/opt/ods LLM_BACKEND_REQUESTED=""
    eval "$1"
    eval "resume() {
$resume_block
printf '%s\n' \"\$resume_command\"
}"
    resume
)
[[ "$(resume_command 'LLM_BACKEND_REQUESTED=vllm')" == 'cd "/opt/ods" && ./install.sh --llm-backend vllm' \
    && "$(resume_command 'LLM_BACKEND_REQUESTED=llama-server ODS_RESELECT_MODEL=true')" \
        == 'cd "/opt/ods" && ./install.sh --reselect-model --llm-backend llama-server' \
    && "$(resume_command ':')" == 'cd "/opt/ods" && ./install.sh' ]] \
    || fail "the Secure Boot resume command must keep --llm-backend: $(resume_command 'LLM_BACKEND_REQUESTED=vllm')"
pass "the Secure Boot resume command keeps the --llm-backend choice"

fi

# ── 2. The route rule ───────────────────────────────────────────────────────
if only 2; then
blocker() { ods_vllm_route_blocker "$@"; }
[[ -z "$(blocker Linux false nvidia local "" "" false)" ]] || fail "Linux NVIDIA local must allow vLLM"
check_blocked() {
    local expected="$1" reason
    shift
    reason="$(blocker "$@")"
    [[ "$reason" == *"$expected"* ]] || fail "route $* must be refused with '$expected', got '$reason'"
}
check_blocked 'only on Linux' Darwin false nvidia local "" "" false
check_blocked 'WSL' Linux true nvidia local "" "" false
check_blocked 'cloud mode' Linux false nvidia cloud "" "" false
check_blocked 'ODS_MODE=hybrid, and the vLLM engine runs only in local mode' Linux false nvidia hybrid "" "" false
check_blocked 'ODS_MODE=lemonade' Linux false nvidia lemonade "" "" false
check_blocked 'external model server (http://10.0.0.5:11434)' Linux false nvidia local http://10.0.0.5:11434 "" false
check_blocked 'host-native llama-server (http://localhost:8080)' Linux false nvidia local "" http://localhost:8080 false
check_blocked 'gateway-only' Linux false nvidia local "" "" true
for backend in amd cpu apple intel sycl jetson; do
    check_blocked "GPU backend is $backend" Linux false "$backend" local "" "" false
done
pass "vLLM is refused on macOS, WSL, cloud, hybrid, external, host-native, gateway-only and every non-NVIDIA backend"

ods_vllm_auto_considered false "" false || fail "a fresh install must consider vLLM"
ods_vllm_auto_considered true vllm false || fail "a rerun of a vLLM install must consider vLLM"
ods_vllm_auto_considered true llama-server true || fail "--reselect-model must consider vLLM"
! ods_vllm_auto_considered true llama-server false || fail "a rerun of a llama.cpp install must keep llama.cpp"
! ods_vllm_auto_considered true "" false || fail "a rerun of an older install must keep llama.cpp"
pass "without --llm-backend, only fresh installs, vLLM reruns and --reselect-model consider vLLM"

[[ "$pinned" =~ ^vllm/vllm-openai:v[0-9][^@]*@sha256:[0-9a-f]{64}$ ]] || fail "the vLLM image pin was not read: $pinned"
grep -qF "\${VLLM_IMAGE:-$pinned}" "$ROOT_DIR/docker-compose.vllm.yml" \
    || fail "the installer's VLLM_IMAGE must equal docker-compose.vllm.yml's default"
printf '{"entries":[{"id":"vllm.vllm-server","value":"vllm/vllm-openai:latest"}]}\n' > "$tmp/unpinned-lock.json"
! ods_vllm_image_from_lock "$tmp/unpinned-lock.json" >/dev/null || fail "an image without a digest must be refused"
pass "VLLM_IMAGE comes from the dependency lock's digest pin, the compose default"

fi

# ── 3. Phase 02's decision with the real selector and catalog ───────────────
if only 3; then
refused_fn="$(extract '/^_phase02_vllm_refused() {$/,/^}$/p' "$PHASE02")"
kept_fn="$(extract '/^_phase02_vllm_kept() {$/,/^}$/p' "$PHASE02")"
export_fn="$(extract '/^_phase02_export_llm_backend() {$/,/^}$/p' "$PHASE02")"
decision="$(extract '/^_phase02_choose_llm_backend() {$/,/^_phase02_export_llm_backend$/p' "$PHASE02")"
load_block="$(extract '/^# The vLLM pick replaces the tier map/,/^fi$/p' "$PHASE02")"
# Keeping a vLLM installation's model, active-model preservation and the
# report of the choice, which follows it.
keep_fns="$(extract '/^# A rerun of a vLLM installation whose model cannot be kept/,/^# The tier\/catalog result is a recommendation\./p' "$PHASE02")"
preservation="$(extract '/^# The tier\/catalog result is a recommendation\./,/^unset _native_python$/p' "$PHASE02")"
shown="$(extract '/^# The model server and the model it serves, now that preservation is final\.$/,/^fi$/p' "$PHASE02")"
grep -q '^LLM_BACKEND=llama-server$' "$PHASE02" || fail "phase 02 must start every run on llama-server"
# Cloud mode returns before the decision; leaving vLLM still exports the choice.
cloud_block="$(extract '/^if \[\[ "\${ODS_MODE:-local}" == "cloud" \]\]; then$/,/^    resolve_compose_config$/p' "$PHASE02")"
[[ "$cloud_block" == *'_phase02_export_llm_backend'* ]] \
    || fail "phase 02's cloud path must export the choice before resolving Compose"

fakebin="$tmp/bin"
mkdir -p "$fakebin"
cat > "$fakebin/nvidia-smi" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$FAKE_NVIDIA_SMI_CALLS"
[[ "${FAKE_NVIDIA_SMI_FAIL:-false}" != true ]] || { echo "NVIDIA-SMI has failed" >&2; exit 9; }
printf '%b' "$FAKE_GPU_ROWS"
EOF
# Python for phase 02: scripts/preserve-active-model.py answers from the
# FAKE_PRESERVE_* settings (its vLLM contract lives on the runtime side);
# everything else, the real model selector included, runs on real Python.
cat > "$fakebin/python-shim" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == */scripts/preserve-active-model.py ]]; then
    printf '%s\n' "${*:2}" >> "$FAKE_PRESERVE_CALLS"
    [[ -z "${FAKE_PRESERVE_STDERR:-}" ]] || printf '%s\n' "$FAKE_PRESERVE_STDERR" >&2
    [[ -z "${FAKE_PRESERVE_OUT:-}" ]] || printf '%b' "$FAKE_PRESERVE_OUT"
    exit "${FAKE_PRESERVE_STATUS:-0}"
fi
exec "$FAKE_REAL_PYTHON" "$@"
EOF
chmod +x "$fakebin/nvidia-smi" "$fakebin/python-shim"
real_python="$(SCRIPT_DIR="$ROOT_DIR"; source "$ROOT_DIR/installers/lib/model-selector.sh"; ods_model_selector_python)"
[[ -n "$real_python" ]] || fail "the real model selector needs Python"
DGX_ROWS='GPU-aaaaaaaa-gb300, 10.3, 256703\nGPU-bbbbbbbb-rtx6000, 12.0, 97887\n'
RTX_ROWS='GPU-cccccccc-4090, 8.9, 24564\n'

# decide NAME [VAR=value...]: run phase 02's decision, active-model
# preservation and the report in a fresh shell with the given inputs;
# NAME/{out,err,rc,calls,preserve-calls,install.log} hold the evidence.
decide() {
    local name="$1" rc=0 dir="$tmp/$1"
    shift
    mkdir -p "$dir/install"
    : > "$dir/calls"
    : > "$dir/preserve-calls"
    {
        printf '%s\n' 'set -euo pipefail'
        printf 'SCRIPT_DIR=%q\nINSTALL_DIR=%q\nLOG_FILE=%q\n' "$ROOT_DIR" "$dir/install" "$dir/install.log"
        printf 'export FAKE_NVIDIA_SMI_CALLS=%q PATH=%q\n' "$dir/calls" "$fakebin:$PATH"
        printf 'export FAKE_PRESERVE_CALLS=%q FAKE_REAL_PYTHON=%q FAKE_PYTHON_SHIM=%q\n' \
            "$dir/preserve-calls" "$real_python" "$fakebin/python-shim"
        cat <<'EOF'
source "$SCRIPT_DIR/lib/safe-env.sh"
source "$SCRIPT_DIR/installers/lib/external-services.sh"
source "$SCRIPT_DIR/installers/lib/model-selector.sh"
source "$SCRIPT_DIR/installers/lib/llm-backend.sh"
ods_model_selector_python() { printf '%s' "$FAKE_PYTHON_SHIM"; }
ods_native_llm_requested() { [[ -n "${NATIVE_LLM_BASE_URL:-}" ]]; }
log() { printf 'LOG %s\n' "$*" >> "$LOG_FILE"; }
ai_ok() { printf 'OK %s\n' "$*"; }
error() { printf 'ERROR %s\n' "$*" >&2; }
ods_is_wsl_host() { [[ "${FAKE_WSL:-false}" == true ]]; }
uname() { printf '%s\n' "${FAKE_KERNEL:-Linux}"; }
GPU_BACKEND=nvidia
ODS_MODE=local
HOST_ARCH=arm64
EXTERNAL_LLM_URL=""
NATIVE_LLM_BASE_URL=""
ODS_GATEWAY_ONLY=false
ODS_RESELECT_MODEL=false
LLM_BACKEND_REQUESTED=""
# The llama.cpp selector block (not run here) leaves its Python for preservation.
_selector_python="$FAKE_PYTHON_SHIM"
export FAKE_GPU_ROWS='GPU-aaaaaaaa-gb300, 10.3, 256703\nGPU-bbbbbbbb-rtx6000, 12.0, 97887\n'
EOF
        printf '%s\n' "$@"
        printf '%s\n' 'LLM_BACKEND=llama-server'
        printf '%s\n' "$refused_fn" "$kept_fn" "$export_fn" "$decision"
        # The tier map ran before the pick is loaded.
        printf '%s\n' 'LLM_MODEL=tier-model GGUF_FILE=tier.gguf GGUF_URL=https://example.invalid/tier.gguf GGUF_SHA256=abc'
        printf '%s\n' 'MAX_CONTEXT=32768 LLM_MODEL_SIZE_MB=20000'
        printf '%s\n' "$load_block" "$keep_fns" "$preservation" "$shown"
        cat <<'EOF'
printf 'BACKEND=%s\n' "$LLM_BACKEND"
printf 'EXPORTED=%s\n' "$(bash -c 'printf %s "${LLM_BACKEND:-}"')"
printf 'MODEL=%s\nGGUF_FILE=%s\nGGUF_URL=%s\nMAX_CONTEXT=%s\nSIZE_MB=%s\n' "$LLM_MODEL" "$GGUF_FILE" "$GGUF_URL" "$MAX_CONTEXT" "$LLM_MODEL_SIZE_MB"
printf 'VLLM_MODEL_DIR=%s\nVLLM_SERVED_MODEL=%s\nVLLM_GPU_UUID=%s\nVLLM_IMAGE=%s\nVLLM_ARGS=%s\n' \
    "${VLLM_MODEL_DIR:-}" "${VLLM_SERVED_MODEL:-}" "${VLLM_GPU_UUID:-}" "${VLLM_IMAGE:-}" "${VLLM_ARGS:-}"
printf 'VLLM_SHARE=%s\nSELECTION_SOURCE=%s\nRECOMMENDED=%s\n' \
    "${VLLM_GPU_MEMORY_UTILIZATION:-}" "${MODEL_SELECTION_SOURCE:-}" "${INSTALLER_RECOMMENDED_MODEL:-}"
EOF
    } > "$dir/run.bash"
    bash "$dir/run.bash" >"$dir/out" 2>"$dir/err" || rc=$?
    printf '%s\n' "$rc" > "$dir/rc"
}
value() { sed -n "s/^$2=//p" "$tmp/$1/out"; }
rc_of() { cat "$tmp/$1/rc"; }
calls() { wc -l < "$tmp/$1/calls" | tr -d ' '; }
preserve_calls() { wc -l < "$tmp/$1/preserve-calls" | tr -d ' '; }
# expect_llama NAME [EXPORTED]: llama.cpp with the tier map untouched. The
# environment stays as it was (nothing exported) unless the run leaves vLLM.
expect_llama() {
    local name="$1" exported="${2:-}"
    [[ "$(rc_of "$name")" == 0 ]] || fail "$name: the decision failed: $(cat "$tmp/$name/err")"
    [[ "$(value "$name" BACKEND)" == llama-server ]] || fail "$name: expected llama.cpp, got $(value "$name" BACKEND)"
    [[ "$(value "$name" EXPORTED)" == "$exported" ]] \
        || fail "$name: expected LLM_BACKEND '${exported}' in the environment, got '$(value "$name" EXPORTED)'"
    [[ "$(value "$name" MODEL)" == tier-model && "$(value "$name" GGUF_FILE)" == tier.gguf \
        && "$(value "$name" MAX_CONTEXT)" == 32768 && -z "$(value "$name" VLLM_GPU_UUID)" \
        && -z "$(value "$name" VLLM_IMAGE)" ]] \
        || fail "$name: the llama.cpp path must keep the tier map's model untouched: $(cat "$tmp/$name/out")"
}
expect_refused() {
    local name="$1" reason="$2"
    [[ "$(rc_of "$name")" != 0 ]] || fail "$name: --llm-backend vllm was accepted: $(cat "$tmp/$name/out")"
    grep -qF -- "--llm-backend vllm cannot be used here: " "$tmp/$name/err" && grep -qF -- "$reason" "$tmp/$name/err" \
        || fail "$name: expected the reason '$reason', got: $(cat "$tmp/$name/err")"
    grep -q 'discord.gg' "$tmp/$name/err" || fail "$name: the refusal must name the Discord help link"
}
# expect_kept_refused NAME REASON: a rerun of a vLLM install stopped instead
# of becoming a llama.cpp install, naming why and how to choose again.
expect_kept_refused() {
    local name="$1" reason="$2"
    [[ "$(rc_of "$name")" != 0 ]] || fail "$name: a rerun of a vLLM install left vLLM: $(cat "$tmp/$name/out")"
    grep -qF 'ERROR This installation runs its model on the vLLM engine, which this run cannot use: ' "$tmp/$name/err" \
        && grep -qF -- "$reason" "$tmp/$name/err" \
        && grep -qF -- 'rerun with --llm-backend llama-server, or with --reselect-model' "$tmp/$name/err" \
        || fail "$name: expected the kept-vLLM refusal naming '$reason', got: $(cat "$tmp/$name/err")"
    grep -q 'discord.gg' "$tmp/$name/err" || fail "$name: the refusal must name the Discord help link"
    [[ "$(preserve_calls "$name")" == 0 ]] || fail "$name: the model must not be read once the route is refused"
}

decide dgx-auto
[[ "$(rc_of dgx-auto)" == 0 ]] || fail "the DGX Station decision failed: $(cat "$tmp/dgx-auto/err")"
[[ "$(value dgx-auto BACKEND)" == vllm && "$(value dgx-auto EXPORTED)" == vllm ]] \
    || fail "the DGX Station must choose vLLM: $(cat "$tmp/dgx-auto/out")"
[[ "$(value dgx-auto VLLM_GPU_UUID)" == GPU-aaaaaaaa-gb300 ]] || fail "vLLM must own the GB300, not the RTX PRO 6000"
# The expected pick is the catalog's highest-priority vLLM entry that fits the GB300
# (model_vllm.rank_entries), so a priority change in the catalog needs no edit here.
read -r want_model want_served want_dir want_size < <("$real_python" - "$ROOT_DIR" <<'PY'
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root / "extensions/services/dashboard-api"))
from model_vllm import Gpu, model_dir_name, rank_entries
models = json.loads((root / "config/model-library.json").read_text(encoding="utf-8"))["models"]
best = rank_entries(models, [Gpu("GPU-aaaaaaaa-gb300", "10.3", 256703)])[0].model
print(best["llm_model_name"], best["served_model_name"], model_dir_name(best), best["size_mb"])
PY
)
want_size="${want_size%$'\r'}"   # a Windows Python ends the line with CRLF
[[ -n "$want_model" ]] || fail "the catalog must hold a vLLM model that fits the GB300"
[[ "$(value dgx-auto MODEL)" == "$want_model" \
    && "$(value dgx-auto VLLM_SERVED_MODEL)" == "$want_served" \
    && "$(value dgx-auto VLLM_MODEL_DIR)" == "$want_dir" ]] \
    || fail "the DGX Station must get the highest-priority vLLM model ($want_model): $(cat "$tmp/dgx-auto/out")"
[[ -z "$(value dgx-auto GGUF_FILE)" && -z "$(value dgx-auto GGUF_URL)" && "$(value dgx-auto MAX_CONTEXT)" == 131072 \
    && "$(value dgx-auto SIZE_MB)" == "$want_size" ]] \
    || fail "the vLLM pick must replace the tier map's GGUF: $(cat "$tmp/dgx-auto/out")"
[[ "$(value dgx-auto VLLM_IMAGE)" == "$pinned" ]] || fail "VLLM_IMAGE must be the locked pin"
[[ "$(value dgx-auto VLLM_ARGS)" == *'--max-model-len 131072'* ]] || fail "VLLM_ARGS must carry the catalog arguments"
[[ "$(calls dgx-auto)" == 1 ]] && grep -qx -- '--query-gpu=uuid,compute_cap,memory.total --format=csv,noheader,nounits' "$tmp/dgx-auto/calls" \
    || fail "the decision must read each GPU's uuid, compute capability and memory once"
grep -qx "OK Model server: vLLM engine, serving $want_served on GPU GPU-aaaaaaaa-gb300" "$tmp/dgx-auto/out" \
    || fail "the choice must be shown: $(cat "$tmp/dgx-auto/out")"
pass "a fresh DGX Station install chooses vLLM on the GB300 with the catalog's top vLLM pick ($want_served)"

decide b200 "export FAKE_GPU_ROWS='GPU-dddddddd-b200, 10.0, 183359\n'"
[[ "$(value b200 BACKEND)" == vllm && "$(value b200 VLLM_GPU_UUID)" == GPU-dddddddd-b200 ]] \
    || fail "one B200 with room for a vLLM model must choose vLLM: $(cat "$tmp/b200/out" "$tmp/b200/err")"
pass "other datacenter Blackwell GPUs choose vLLM where a model fits"

decide rtx-auto "export FAKE_GPU_ROWS='$RTX_ROWS'"
expect_llama rtx-auto
grep -q 'LOG Model server: llama.cpp (no vLLM catalog model fits this host' "$tmp/rtx-auto/install.log" \
    || fail "the reason vLLM was not chosen must reach the install log"
[[ ! -s "$tmp/rtx-auto/err" ]] || fail "a host without a vLLM fit must not print anything: $(cat "$tmp/rtx-auto/err")"
decide spark-auto "export FAKE_GPU_ROWS='GPU-eeeeeeee-gb10, 12.1, [N/A]\n'"
expect_llama spark-auto
decide rtx5090-auto "export FAKE_GPU_ROWS='GPU-gggggggg-5090, 12.0, 32607\n'"
expect_llama rtx5090-auto
pass "without a vLLM fit (RTX 4090, DGX Spark, RTX 5090) the llama.cpp path is unchanged and quiet"

# A workstation whose only GPU is an RTX PRO 6000 gets the catalog's RTX PRO
# 6000-class vLLM model and that entry's own memory share, which leaves room
# for ComfyUI and voice on the same GPU.
decide pro6000-auto "export FAKE_GPU_ROWS='GPU-ffffffff-rtx6000, 12.0, 97887\n'"
read -r p6k_model p6k_share < <("$real_python" - "$ROOT_DIR" <<'PY'
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root / "extensions/services/dashboard-api"))
from model_vllm import Gpu, rank_entries
models = json.loads((root / "config/model-library.json").read_text(encoding="utf-8"))["models"]
best = rank_entries(models, [Gpu("GPU-ffffffff-rtx6000", "12.0", 97887)])[0].model
print(best["llm_model_name"], f"{best['gpu_memory_utilization']:g}")
PY
)
p6k_share="${p6k_share%$'\r'}"   # a Windows Python ends the line with CRLF
[[ "$(rc_of pro6000-auto)" == 0 && "$(value pro6000-auto BACKEND)" == vllm \
    && "$(value pro6000-auto MODEL)" == "$p6k_model" && "$(value pro6000-auto VLLM_GPU_UUID)" == GPU-ffffffff-rtx6000 \
    && "$(value pro6000-auto VLLM_SHARE)" == "$p6k_share" && "$p6k_share" == 0.5 ]] \
    || fail "one RTX PRO 6000 must choose vLLM with its entry's memory share: $(cat "$tmp/pro6000-auto/out" "$tmp/pro6000-auto/err")"
pass "a lone RTX PRO 6000 chooses vLLM ($p6k_model) at its entry's $p6k_share memory share"

decide rtx-forced "export FAKE_GPU_ROWS='$RTX_ROWS'" 'LLM_BACKEND_REQUESTED=vllm'
expect_refused rtx-forced 'no vLLM catalog model fits this host'
grep -qF 'needs one GPU with compute capability 10.0 or 10.3' "$tmp/rtx-forced/err" \
    || fail "the forced refusal must say what a vLLM model needs"
pass "--llm-backend vllm without a fit stops and says what a vLLM model needs"

decide dgx-llama 'LLM_BACKEND_REQUESTED=llama-server'
expect_llama dgx-llama
[[ "$(calls dgx-llama)" == 0 ]] || fail "--llm-backend llama-server must not query the GPUs for vLLM"
pass "--llm-backend llama-server never considers vLLM, even where it fits"

for route in \
    "wsl|FAKE_WSL=true|this is WSL" \
    "macos|FAKE_KERNEL=Darwin|only on Linux" \
    "amd|GPU_BACKEND=amd|GPU backend is amd" \
    "cpu|GPU_BACKEND=cpu|GPU backend is cpu" \
    "cloud|ODS_MODE=cloud|cloud mode" \
    "hybrid|ODS_MODE=hybrid|runs only in local mode" \
    "external|EXTERNAL_LLM_URL=http://127.0.0.1:11434|external model server" \
    "native|NATIVE_LLM_BASE_URL=http://127.0.0.1:8080|host-native llama-server" \
    "gateway|ODS_GATEWAY_ONLY=true|gateway-only" \
    "no-catalog|ODS_DISABLE_CATALOG_MODEL_SELECTOR=true|ODS_DISABLE_CATALOG_MODEL_SELECTOR"; do
    IFS='|' read -r name assignment reason <<< "$route"
    decide "$name-auto" "$assignment"
    expect_llama "$name-auto"
    [[ "$(calls "$name-auto")" == 0 ]] || fail "$name: a route that cannot run vLLM must not query the GPUs"
    decide "$name-forced" "$assignment" 'LLM_BACKEND_REQUESTED=vllm'
    expect_refused "$name-forced" "$reason"
done
pass "WSL, macOS, AMD, CPU, cloud, hybrid, external, host-native, gateway-only and a disabled catalog never use vLLM"

retained() { printf '%s\n' "$@" > "$tmp/$name/install/.env"; }
name=rerun-llama; mkdir -p "$tmp/$name/install"; retained 'LLM_BACKEND=llama-server' 'GGUF_FILE=Qwen3.6-35B-A3B-UD-Q4_K_M.gguf'
decide rerun-llama
expect_llama rerun-llama
[[ "$(calls rerun-llama)" == 0 ]] || fail "a rerun of a llama.cpp install must not query the GPUs"
[[ "$(preserve_calls rerun-llama)" == 1 ]] && grep -q -- '--local-model$' "$tmp/rerun-llama/preserve-calls" \
    || fail "a rerun of a llama.cpp install must still preserve its active model"
name=rerun-reselect; mkdir -p "$tmp/$name/install"; retained 'LLM_BACKEND=llama-server'
decide rerun-reselect 'ODS_RESELECT_MODEL=true'
[[ "$(value rerun-reselect BACKEND)" == vllm ]] || fail "--reselect-model must let a rerun choose vLLM"
name=rerun-forced; mkdir -p "$tmp/$name/install"; retained 'LLM_BACKEND=llama-server' 'GGUF_FILE=Qwen3.6-35B-A3B-UD-Q4_K_M.gguf'
decide rerun-forced 'LLM_BACKEND_REQUESTED=vllm'
[[ "$(rc_of rerun-forced)" == 0 && "$(value rerun-forced BACKEND)" == vllm \
    && "$(value rerun-forced MODEL)" == "$want_model" && "$(preserve_calls rerun-forced)" == 0 ]] \
    || fail "--llm-backend vllm must move a llama.cpp install to the vLLM pick, not keep its GGUF: $(cat "$tmp/rerun-forced/out" "$tmp/rerun-forced/err")"
name=retained-external; mkdir -p "$tmp/$name/install"; retained 'LLM_BACKEND=external' 'EXTERNAL_LLM_URL=http://127.0.0.1:1234'
decide retained-external 'ODS_RESELECT_MODEL=true'
expect_llama retained-external
name=retained-external-off; mkdir -p "$tmp/$name/install"; retained 'LLM_BACKEND=external' 'EXTERNAL_LLM_URL=http://127.0.0.1:1234'
decide retained-external-off 'ODS_RESELECT_MODEL=true' 'EXTERNAL_LLM_DISABLE=true'
[[ "$(value retained-external-off BACKEND)" == vllm ]] || fail "--no-external-llm must free the route for vLLM"
pass "a llama.cpp install's rerun keeps llama.cpp unless asked; --llm-backend vllm or --reselect-model moves it to the vLLM pick; an API route blocks vLLM"

# A rerun of a vLLM install keeps its installed model: GLM-5.3 here, while
# the fresh pick for the GB300 is Qwen 3.8, which stays the recommendation.
GLM_DIR='nvidia--GLM-5.3-Flash-NVFP4@da920bb0'
vllm_install() {
    retained 'LLM_BACKEND=vllm' 'LLM_MODEL=glm-5.3-flash-nvfp4' "VLLM_MODEL_DIR=$GLM_DIR" \
        'VLLM_SERVED_MODEL=GLM-5.3-Flash-NVFP4' 'VLLM_GPU_UUID=GPU-aaaaaaaa-gb300' 'VLLM_GPU_MEMORY_UTILIZATION=0.85'
}
# scripts/preserve-active-model.py's vLLM contract for that .env: every key,
# in its order, as its shell_value() quotes them.
GLM_CONTRACT='LLM_BACKEND="vllm"\nLLM_MODEL="glm-5.3-flash-nvfp4"\nGGUF_FILE=""\nGGUF_URL=""\nGGUF_SHA256=""\nMAX_CONTEXT="131072"\nLLM_MODEL_SIZE_MB="204476"\nMODEL_SELECTION_SOURCE="dashboard"\nVLLM_MODEL_DIR="'"$GLM_DIR"'"\nVLLM_SERVED_MODEL="GLM-5.3-Flash-NVFP4"\nVLLM_ARGS="--max-model-len 131072 --kv-cache-dtype fp8"\nVLLM_GPU_UUID="GPU-aaaaaaaa-gb300"\nVLLM_GPU_MEMORY_UTILIZATION="0.85"\n'
name=rerun-vllm; mkdir -p "$tmp/$name/install"; vllm_install
decide rerun-vllm "export FAKE_PRESERVE_OUT='$GLM_CONTRACT'"
[[ "$(rc_of rerun-vllm)" == 0 ]] || fail "a rerun of a vLLM install failed: $(cat "$tmp/rerun-vllm/err")"
[[ "$(value rerun-vllm BACKEND)" == vllm && "$(value rerun-vllm EXPORTED)" == vllm ]] \
    || fail "a rerun of a vLLM install must stay on vLLM: $(cat "$tmp/rerun-vllm/out")"
[[ "$(value rerun-vllm MODEL)" == glm-5.3-flash-nvfp4 && "$(value rerun-vllm VLLM_MODEL_DIR)" == "$GLM_DIR" \
    && "$(value rerun-vllm VLLM_SERVED_MODEL)" == GLM-5.3-Flash-NVFP4 \
    && "$(value rerun-vllm VLLM_GPU_UUID)" == GPU-aaaaaaaa-gb300 && "$(value rerun-vllm VLLM_SHARE)" == 0.85 \
    && "$(value rerun-vllm VLLM_ARGS)" == '--max-model-len 131072 --kv-cache-dtype fp8' \
    && -z "$(value rerun-vllm GGUF_FILE)" && "$(value rerun-vllm MAX_CONTEXT)" == 131072 \
    && "$(value rerun-vllm SIZE_MB)" == 204476 && "$(value rerun-vllm SELECTION_SOURCE)" == dashboard ]] \
    || fail "a rerun must keep the installed vLLM model, folder, served id, GPU and memory share: $(cat "$tmp/rerun-vllm/out")"
[[ "$(value rerun-vllm RECOMMENDED)" == "$want_model" ]] \
    || fail "the fresh vLLM pick must stay the recommendation, not the active model: $(cat "$tmp/rerun-vllm/out")"
[[ "$(preserve_calls rerun-vllm)" == 1 ]] \
    && grep -qF -- "--env $tmp/rerun-vllm/install/.env " "$tmp/rerun-vllm/preserve-calls" \
    && grep -qF -- "--models-dir $tmp/rerun-vllm/install/data/models " "$tmp/rerun-vllm/preserve-calls" \
    && grep -q -- '--local-model$' "$tmp/rerun-vllm/preserve-calls" \
    || fail "the vLLM model must be read once from the installation's .env and models: $(cat "$tmp/rerun-vllm/preserve-calls")"
grep -qx 'OK Model server: vLLM engine, serving GLM-5.3-Flash-NVFP4 on GPU GPU-aaaaaaaa-gb300' "$tmp/rerun-vllm/out" \
    || fail "the report must name the kept model and GPU: $(cat "$tmp/rerun-vllm/out")"
grep -qF "LOG Kept this installation's vLLM model across installer rerun: glm-5.3-flash-nvfp4 ($GLM_DIR) on GPU GPU-aaaaaaaa-gb300, selected by dashboard" \
    "$tmp/rerun-vllm/install.log" || fail "keeping the vLLM model must reach the install log"
pass "a rerun of a vLLM install keeps its model, folder, GPU and memory share; the fresh pick stays the recommendation"

# The kept model never takes the fresh pick's memory share: kept without a
# share of its own, it stays on compose's default even where the fresh pick
# (the RTX PRO 6000 entry here) sets one.
share_line='VLLM_GPU_MEMORY_UTILIZATION="0.85"\n'
GLM_CONTRACT_NO_SHARE="${GLM_CONTRACT%"$share_line"}"
[[ "$GLM_CONTRACT_NO_SHARE" != "$GLM_CONTRACT" ]] || fail "the contract fixture must end with the share line"
name=rerun-vllm-own-share; mkdir -p "$tmp/$name/install"
retained 'LLM_BACKEND=vllm' 'LLM_MODEL=glm-5.3-flash-nvfp4' "VLLM_MODEL_DIR=$GLM_DIR" \
    'VLLM_SERVED_MODEL=GLM-5.3-Flash-NVFP4' 'VLLM_GPU_UUID=GPU-aaaaaaaa-gb300'
decide rerun-vllm-own-share "export FAKE_GPU_ROWS='GPU-ffffffff-rtx6000, 12.0, 97887\n'" \
    "export FAKE_PRESERVE_OUT='$GLM_CONTRACT_NO_SHARE'"
[[ "$(rc_of rerun-vllm-own-share)" == 0 && "$(value rerun-vllm-own-share MODEL)" == glm-5.3-flash-nvfp4 \
    && -z "$(value rerun-vllm-own-share VLLM_SHARE)" \
    && "$(value rerun-vllm-own-share RECOMMENDED)" == "$p6k_model" ]] \
    || fail "a kept vLLM model must not take the fresh pick's memory share: $(cat "$tmp/rerun-vllm-own-share/out" "$tmp/rerun-vllm-own-share/err")"
pass "a kept vLLM model keeps only its own memory share, never the fresh pick's"

name=rerun-vllm-incomplete; mkdir -p "$tmp/$name/install"; vllm_install
decide rerun-vllm-incomplete 'export FAKE_PRESERVE_STATUS=2' \
    "export FAKE_PRESERVE_STDERR='This installation runs its model on the vLLM engine, but its saved model is not a complete vLLM catalog snapshot for the GPU it names; refusing to replace it.'"
[[ "$(rc_of rerun-vllm-incomplete)" != 0 ]] \
    || fail "exit 2 from the model helper must stop the rerun: $(cat "$tmp/rerun-vllm-incomplete/out")"
grep -qF "ERROR This installation runs glm-5.3-flash-nvfp4 on the vLLM engine, but its saved vLLM model is incomplete or is no longer this release's catalog model" \
    "$tmp/rerun-vllm-incomplete/err" \
    && grep -qF -- 'rerun the installer with --reselect-model' "$tmp/rerun-vllm-incomplete/err" \
    && grep -qF "data/models/$GLM_DIR was moved or deleted, restore it" "$tmp/rerun-vllm-incomplete/err" \
    && grep -q 'discord.gg' "$tmp/rerun-vllm-incomplete/err" \
    || fail "exit 2 must stop with the cause, --reselect-model, the model folder and the Discord link: $(cat "$tmp/rerun-vllm-incomplete/err")"
grep -qF 'refusing to replace it' "$tmp/rerun-vllm-incomplete/install.log" \
    || fail "the helper's own reason must reach the install log"
! grep -q '^OK Model server' "$tmp/rerun-vllm-incomplete/out" || fail "a stopped rerun must not report a model server"
name=rerun-vllm-old-helper; mkdir -p "$tmp/$name/install"; vllm_install
decide rerun-vllm-old-helper
[[ "$(rc_of rerun-vllm-old-helper)" != 0 ]] \
    && grep -qF 'but ODS could not read its saved vLLM model (scripts/preserve-active-model.py exit 0;' "$tmp/rerun-vllm-old-helper/err" \
    || fail "a helper without a vLLM contract must stop the rerun: $(cat "$tmp/rerun-vllm-old-helper/out" "$tmp/rerun-vllm-old-helper/err")"
name=rerun-vllm-partial; mkdir -p "$tmp/$name/install"; vllm_install
decide rerun-vllm-partial "export FAKE_PRESERVE_OUT='${GLM_CONTRACT%VLLM_GPU_UUID=*}'"
[[ "$(rc_of rerun-vllm-partial)" != 0 ]] && grep -qF 'exit 0 with an incomplete contract' "$tmp/rerun-vllm-partial/err" \
    || fail "a contract without VLLM_GPU_UUID must stop, not keep the pick's GPU: $(cat "$tmp/rerun-vllm-partial/out" "$tmp/rerun-vllm-partial/err")"
pass "a vLLM model that cannot be kept (helper exit 2, no contract, a missing key) stops the rerun with the cause and how to recover"

name=rerun-vllm-gone; mkdir -p "$tmp/$name/install"; vllm_install
decide rerun-vllm-gone "export FAKE_GPU_ROWS='$RTX_ROWS'"
expect_kept_refused rerun-vllm-gone 'no vLLM catalog model fits this host'
name=rerun-vllm-cpu; mkdir -p "$tmp/$name/install"; vllm_install
decide rerun-vllm-cpu 'GPU_BACKEND=cpu'
expect_kept_refused rerun-vllm-cpu 'the GPU backend is cpu'
name=rerun-vllm-smi; mkdir -p "$tmp/$name/install"; vllm_install
decide rerun-vllm-smi 'export FAKE_NVIDIA_SMI_FAIL=true'
expect_kept_refused rerun-vllm-smi 'nvidia-smi could not list'
name=rerun-vllm-hybrid; mkdir -p "$tmp/$name/install"; vllm_install
decide rerun-vllm-hybrid 'ODS_MODE=hybrid'
expect_kept_refused rerun-vllm-hybrid 'runs only in local mode'
name=rerun-vllm-cloud; mkdir -p "$tmp/$name/install"; vllm_install
decide rerun-vllm-cloud 'ODS_MODE=cloud'
expect_kept_refused rerun-vllm-cloud 'cloud mode'
pass "a vLLM install's rerun stops instead of becoming llama.cpp when nothing fits, the NVIDIA GPU or nvidia-smi is gone, or the mode is not local"

name=rerun-vllm-forced-off; mkdir -p "$tmp/$name/install"; vllm_install
decide rerun-vllm-forced-off 'LLM_BACKEND_REQUESTED=llama-server' "export FAKE_PRESERVE_OUT='$GLM_CONTRACT'"
expect_llama rerun-vllm-forced-off llama-server
[[ "$(preserve_calls rerun-vllm-forced-off)" == 0 ]] || fail "leaving vLLM must not load the vLLM model's contract"
grep -qF 'LOG Leaving the vLLM engine' "$tmp/rerun-vllm-forced-off/install.log" || fail "leaving vLLM must be logged"
name=rerun-vllm-reselect; mkdir -p "$tmp/$name/install"; vllm_install
decide rerun-vllm-reselect 'ODS_RESELECT_MODEL=true' "export FAKE_PRESERVE_OUT='$GLM_CONTRACT'"
[[ "$(value rerun-vllm-reselect BACKEND)" == vllm && "$(value rerun-vllm-reselect MODEL)" == "$want_model" \
    && "$(preserve_calls rerun-vllm-reselect)" == 0 ]] \
    || fail "--reselect-model must replace the installed vLLM model with the fresh pick: $(cat "$tmp/rerun-vllm-reselect/out")"
name=rerun-vllm-reselect-gone; mkdir -p "$tmp/$name/install"; vllm_install
decide rerun-vllm-reselect-gone 'ODS_RESELECT_MODEL=true' "export FAKE_GPU_ROWS='$RTX_ROWS'"
expect_llama rerun-vllm-reselect-gone llama-server
pass "--llm-backend llama-server or --reselect-model chooses again without the vLLM model's contract; leaving vLLM exports llama-server"

# Cloud mode returns before the decision; it refuses a vLLM install's rerun
# the same way, and leaving vLLM still exports llama-server.
cloud_run() {
    local name="$1" rc=0 dir="$tmp/$1"
    shift
    mkdir -p "$dir/install"
    {
        printf '%s\n' 'set -euo pipefail'
        printf 'SCRIPT_DIR=%q\nINSTALL_DIR=%q\nLOG_FILE=%q\n' "$ROOT_DIR" "$dir/install" "$dir/install.log"
        cat <<'EOF'
source "$SCRIPT_DIR/lib/safe-env.sh"
source "$SCRIPT_DIR/installers/lib/external-services.sh"
ai() { :; }
error() { printf 'ERROR %s\n' "$*" >&2; }
df() { printf 'Filesystem 1024-blocks Used Available Capacity Mounted\nfake 209715200 0 104857600 0%% /\n'; }
resolve_compose_config() { printf 'EXPORTED=%s\n' "$(bash -c 'printf %s "${LLM_BACKEND:-}"')"; }
ODS_MODE=cloud
ODS_RESELECT_MODEL=false
LLM_BACKEND_REQUESTED=""
LLM_BACKEND=llama-server
EOF
        printf '%s\n' "$@" "$refused_fn" "$kept_fn" "$export_fn" "$cloud_block" 'fi'
    } > "$dir/run.bash"
    bash "$dir/run.bash" >"$dir/out" 2>"$dir/err" || rc=$?
    printf '%s\n' "$rc" > "$dir/rc"
}
name=cloud-vllm; mkdir -p "$tmp/$name/install"; vllm_install
cloud_run cloud-vllm
[[ "$(rc_of cloud-vllm)" != 0 ]] \
    && grep -qF 'ERROR This installation runs its model on the vLLM engine, which this run cannot use: ODS runs in cloud mode' "$tmp/cloud-vllm/err" \
    || fail "cloud mode must refuse a vLLM install's rerun: $(cat "$tmp/cloud-vllm/out" "$tmp/cloud-vllm/err")"
name=cloud-vllm-off; mkdir -p "$tmp/$name/install"; vllm_install
cloud_run cloud-vllm-off 'LLM_BACKEND_REQUESTED=llama-server'
name=cloud-vllm-reselect; mkdir -p "$tmp/$name/install"; vllm_install
cloud_run cloud-vllm-reselect 'ODS_RESELECT_MODEL=true'
for name in cloud-vllm-off cloud-vllm-reselect; do
    [[ "$(rc_of "$name")" == 0 && "$(value "$name" EXPORTED)" == llama-server ]] \
        || fail "$name: leaving vLLM for cloud mode must export llama-server: $(cat "$tmp/$name/out" "$tmp/$name/err")"
done
cloud_run cloud-fresh
[[ "$(rc_of cloud-fresh)" == 0 && -z "$(value cloud-fresh EXPORTED)" ]] \
    || fail "a fresh cloud install must leave LLM_BACKEND unexported: $(cat "$tmp/cloud-fresh/out" "$tmp/cloud-fresh/err")"
cloud_run cloud-forced 'LLM_BACKEND_REQUESTED=vllm'
[[ "$(rc_of cloud-forced)" != 0 ]] && grep -qF -- '--llm-backend vllm cannot be used here: ODS runs in cloud mode' "$tmp/cloud-forced/err" \
    || fail "cloud mode must refuse --llm-backend vllm: $(cat "$tmp/cloud-forced/err")"
pass "cloud mode refuses --llm-backend vllm and a vLLM install's rerun; --llm-backend llama-server or --reselect-model leaves vLLM"

decide smi-auto 'export FAKE_NVIDIA_SMI_FAIL=true'
expect_llama smi-auto
decide smi-forced 'export FAKE_NVIDIA_SMI_FAIL=true' 'LLM_BACKEND_REQUESTED=vllm'
expect_refused smi-forced 'nvidia-smi could not list'
decide crash-auto 'ods_run_vllm_selector() { echo "Traceback: broken catalog" >&2; return 1; }'
[[ "$(rc_of crash-auto)" != 0 ]] && grep -q 'ERROR The vLLM model selector stopped (exit 1): Traceback: broken catalog' "$tmp/crash-auto/err" \
    || fail "a selector failure other than exit 2 must stop the install: $(cat "$tmp/crash-auto/out" "$tmp/crash-auto/err")"
grep -q 'discord.gg' "$tmp/crash-auto/err" || fail "a selector failure must name the Discord help link"
decide bad-lock 'ods_vllm_image_from_lock() { return 1; }'
[[ "$(rc_of bad-lock)" != 0 ]] && grep -q 'names no digest-pinned vLLM image' "$tmp/bad-lock/err" \
    || fail "a missing vLLM image pin must stop the install"
pass "a failed GPU query keeps llama.cpp (or refuses --llm-backend vllm); a selector crash or a missing pin stops"

fi

# ── 4. The loader keeps the vLLM contract ──────────────────────────────────
if only 4; then
(
    # shellcheck source=../lib/safe-env.sh
    source "$ROOT_DIR/lib/safe-env.sh"
    unset LLM_BACKEND VLLM_MODEL_DIR VLLM_SERVED_MODEL VLLM_ARGS VLLM_GPU_UUID VLLM_GPU_MEMORY_UTILIZATION
    load_model_selector_env_from_output < <(printf '%s\n' 'LLM_BACKEND="vllm"' 'VLLM_MODEL_DIR="nvidia--X@01234567"' \
        'VLLM_SERVED_MODEL="X"' 'VLLM_ARGS="--max-model-len 131072 --speculative-config.method=mtp"' \
        'VLLM_GPU_UUID="GPU-1"' 'VLLM_GPU_MEMORY_UTILIZATION="0.85"' 'VLLM_UNKNOWN="no"')
    [[ "$LLM_BACKEND" == vllm && "$VLLM_MODEL_DIR" == 'nvidia--X@01234567' && "$VLLM_SERVED_MODEL" == X \
        && "$VLLM_GPU_UUID" == GPU-1 && "$VLLM_GPU_MEMORY_UTILIZATION" == 0.85 \
        && "$VLLM_ARGS" == '--max-model-len 131072 --speculative-config.method=mtp' && -z "${VLLM_UNKNOWN:-}" ]]
) || fail "load_model_selector_env_from_output must load the vLLM contract keys and no other VLLM_* key"
pass "the selector env loader carries a vLLM contract: LLM_BACKEND and the five VLLM_* keys"

fi

# ── 5. Exporting this run's LLM_BACKEND leaves other selections unchanged ──
if only 5; then
# A run that leaves vLLM exports llama-server (phase 02), and phase 06 then
# exports external for an API: the resolver must select exactly the same files
# as with nothing exported, which is what every other install does.
resolve() {
    (
        cd "$ROOT_DIR"
        env -u LLM_BACKEND -u NATIVE_LLM_BASE_URL -u EXTERNAL_LLM_URL -u ODS_EXTERNAL_LLM_SELECTED \
            -u AMD_INFERENCE_BACKEND -u ODS_GATEWAY_ONLY "$@"
    )
}
compared=0
for fixture in \
    "--tier 1 --gpu-backend nvidia --gpu-count 1 --ods-mode local" \
    "--tier NV_ULTRA --gpu-backend nvidia --gpu-count 2 --ods-mode local" \
    "--tier 4 --gpu-backend nvidia --gpu-count 4 --ods-mode hybrid" \
    "--tier SH_LARGE --gpu-backend amd --gpu-count 1 --ods-mode local" \
    "--tier 2 --gpu-backend amd --gpu-count 2 --ods-mode local" \
    "--tier 1 --gpu-backend cpu --gpu-count 0 --ods-mode local" \
    "--tier 0 --gpu-backend cpu --gpu-count 0 --ods-mode local" \
    "--tier CLOUD --gpu-backend cpu --gpu-count 0 --ods-mode cloud" \
    "--tier AP_PRO --gpu-backend apple --gpu-count 1 --ods-mode local" \
    "--tier ARC --gpu-backend intel --gpu-count 1 --ods-mode local"; do
    read -r -a args <<< "$fixture"
    for mode in files env; do
        [[ "$mode" == files ]] || args+=(--env)
        baseline="$(resolve bash scripts/resolve-compose-stack.sh --script-dir "$ROOT_DIR" "${args[@]}")"
        for exported in llama-server external; do
            got="$(resolve LLM_BACKEND="$exported" bash scripts/resolve-compose-stack.sh --script-dir "$ROOT_DIR" "${args[@]}")"
            [[ "$got" == "$baseline" ]] || fail "LLM_BACKEND=$exported changed the resolver output for $fixture ($mode)"
            compared=$((compared + 1))
        done
    done
done
external_baseline="$(resolve EXTERNAL_LLM_URL=http://127.0.0.1:11434 bash scripts/resolve-compose-stack.sh \
    --script-dir "$ROOT_DIR" --tier 3 --gpu-backend nvidia --ods-mode local)"
[[ "$(resolve EXTERNAL_LLM_URL=http://127.0.0.1:11434 LLM_BACKEND=external bash scripts/resolve-compose-stack.sh \
    --script-dir "$ROOT_DIR" --tier 3 --gpu-backend nvidia --ods-mode local)" == "$external_baseline" ]] \
    || fail "an exported LLM_BACKEND=external changed the external route's stack"
pass "an exported llama-server or external LLM_BACKEND leaves $compared resolver selections byte-identical"

fi

# ── 6. No fast-start GGUF on the vLLM route ────────────────────────────────
if only 6; then
(
    # shellcheck source=../installers/lib/bootstrap-model.sh
    source "$ROOT_DIR/installers/lib/bootstrap-model.sh"
    tier_rank() { printf '4\n'; }
    TIER=NV_ULTRA INSTALL_DIR="$tmp/bootstrap" GGUF_FILE=""
    mkdir -p "$INSTALL_DIR/data/models"
    LLM_BACKEND=llama-server
    bootstrap_needed || exit 1
    LLM_BACKEND=vllm
    ! bootstrap_needed
) || fail "bootstrap_needed must stay true for llama.cpp and false for the vLLM engine"
pass "the bootstrap model is never downloaded for the vLLM engine"

# Phase 02b offers no running Ollama or LM Studio in vLLM's place.
grep -q '"${LLM_BACKEND:-}" != "vllm"' "$ROOT_DIR/installers/phases/02b-external-services.sh" \
    || fail "phase 02b must not offer a detected external model on the vLLM route"
pass "phase 02b does not offer a detected Ollama or LM Studio on the vLLM route"
fi

printf '\nResult: %d passed, 0 failed\n' "$PASS"
