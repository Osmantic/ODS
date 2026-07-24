#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FEATURES_PHASE="$ROOT_DIR/installers/phases/03-features.sh"
ASSIGN_GPUS_SCRIPT="$ROOT_DIR/scripts/assign_gpus.py"

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

[[ -f "$FEATURES_PHASE" ]] || fail "missing phase 03: $FEATURES_PHASE"
[[ -f "$ASSIGN_GPUS_SCRIPT" ]] || fail "missing GPU assignment helper: $ASSIGN_GPUS_SCRIPT"

# The hosted bootstrap is commonly launched as `curl ... | bash`. In that
# shape stdin belongs to the exhausted curl pipeline, so every interactive
# multi-GPU prompt must read from the controlling terminal instead.
prompt_read_count=0
while IFS= read -r prompt_read; do
    prompt_read_count=$((prompt_read_count + 1))
    [[ "$prompt_read" == *"< /dev/tty"* ]] || fail "prompt does not read from /dev/tty: $prompt_read"
done < <(grep -nE '^[[:space:]]*read -rp "  (GPU for|GPUs for|Apply this configuration|Selection \[1\])' "$FEATURES_PHASE")
[[ $prompt_read_count -eq 4 ]] || fail "expected four interactive multi-GPU prompt reads"
pass "all multi-GPU prompts use the controlling terminal"

# util-linux `script` gives the child a real controlling terminal while the
# explicit </dev/null reproduces the bootstrap's exhausted stdin. The static
# contract above remains portable; this behavioral lane runs where util-linux
# is available (including the Linux CI image).
if ! command -v script >/dev/null 2>&1 || ! script --version 2>&1 | grep -qi 'util-linux'; then
    echo "[SKIP] util-linux script is unavailable; PTY behavior not exercised"
    exit 0
fi

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT
mkdir -p "$tmp_dir/scripts"
cp "$ASSIGN_GPUS_SCRIPT" "$tmp_dir/scripts/assign_gpus.py"

cat >"$tmp_dir/harness.sh" <<'HARNESS'
#!/usr/bin/env bash
set -euo pipefail

INTERACTIVE=true
DRY_RUN=false
INSTALL_CHOICE=1
TIER=1
ODS_MODE=local
ENABLE_VOICE=false
ENABLE_WORKFLOWS=false
ENABLE_RAG=false
ENABLE_RECOMMENDED=false
ENABLE_HERMES=false
ENABLE_OPENCLAW=false
ENABLE_COMFYUI=false
ENABLE_APE=false
ENABLE_PERPLEXICA=false
ENABLE_PRIVACY_SHIELD=false
ENABLE_LANGFUSE=false
ENABLE_BRAVE_SEARCH=false
GPU_COUNT=3
GPU_BACKEND=nvidia
HOST_ARCH=amd64
HOST_PAGE_SIZE=4096
INSTALL_DIR="$HARNESS_TMP/install"
SCRIPT_DIR="$HARNESS_TMP"
LLM_MODEL_SIZE_MB=6000
MAX_CONTEXT=8192
VERBOSE=false
DEBUG=false
AMB=
BGRN=
DIM=
GRN=
NC=
RED=
WHT=
GPU_TOPOLOGY_JSON='{
  "vendor": "nvidia",
  "gpu_count": 3,
  "gpus": [
    {"index": 0, "uuid": "GPU-0000", "name": "GTX 1080 Ti", "memory_gb": 11, "memory_free_gb": 11},
    {"index": 1, "uuid": "GPU-1111", "name": "GTX 1080 Ti", "memory_gb": 11, "memory_free_gb": 11},
    {"index": 2, "uuid": "GPU-2222", "name": "GTX 1080", "memory_gb": 8, "memory_free_gb": 8}
  ],
  "links": [
    {"gpu_a": 0, "gpu_b": 1, "rank": 20, "link_type": "PHB", "link_label": "PHB"},
    {"gpu_a": 0, "gpu_b": 2, "rank": 10, "link_type": "SYS", "link_label": "SYS"},
    {"gpu_a": 1, "gpu_b": 2, "rank": 10, "link_type": "SYS", "link_label": "SYS"}
  ]
}'

ods_progress() { :; }
show_phase() { :; }
show_install_menu() { :; }
ai_warn() { :; }
log() { :; }
warn() { printf 'WARN: %s\n' "$*"; }
success() { printf 'SUCCESS: %s\n' "$*"; }
chapter() { :; }
bootline() { :; }
signal() { :; }
get_rank() { printf '10\n'; }
error() {
    printf 'ERROR: %s\n' "$*" >&2
    return 1
}

# shellcheck source=/dev/null
source "$FEATURES_PHASE"

jq -e '.gpu_assignment.services.llama_server.gpus | length > 0' \
    <<<"$GPU_ASSIGNMENT_JSON" >/dev/null
printf 'LLAMA_GPUS=%s\n' \
    "$(jq -r '.gpu_assignment.services.llama_server.gpus | join(",")' <<<"$GPU_ASSIGNMENT_JSON")"
printf 'WHISPER_GPUS=%s\n' \
    "$(jq -r '.gpu_assignment.services.whisper.gpus | join(",")' <<<"$GPU_ASSIGNMENT_JSON")"
printf 'COMFYUI_GPUS=%s\n' \
    "$(jq -r '.gpu_assignment.services.comfyui.gpus | join(",")' <<<"$GPU_ASSIGNMENT_JSON")"
printf 'EMBEDDINGS_GPUS=%s\n' \
    "$(jq -r '.gpu_assignment.services.embeddings.gpus | join(",")' <<<"$GPU_ASSIGNMENT_JSON")"
printf 'PHASE03_COMPLETED\n'
HARNESS
chmod +x "$tmp_dir/harness.sh"

run_with_closed_stdin() {
    local name="$1" input="$2" output
    local command
    shift 2
    output="$tmp_dir/$name.log"
    printf -v command 'env FEATURES_PHASE=%q HARNESS_TMP=%q bash %q </dev/null' \
        "$FEATURES_PHASE" "$tmp_dir" "$tmp_dir/harness.sh"

    printf '%b' "$input" | timeout 20s script -qefc "$command" /dev/null >"$output" 2>&1 \
        || {
            cat "$output" >&2
            fail "$name path failed with closed stdin"
        }
    grep -q 'PHASE03_COMPLETED' "$output" || {
        cat "$output" >&2
        fail "$name path did not complete phase 03"
    }
    for expected in "$@"; do
        grep -Fq "$expected" "$output" || {
            cat "$output" >&2
            fail "$name path is missing expected output: $expected"
        }
    done
}

run_with_closed_stdin automatic '1\n' 'SUCCESS: Assignment complete'
pass "automatic assignment completes with closed stdin"

run_with_closed_stdin automatic-default '\n' 'SUCCESS: Assignment complete'
pass "empty mode selection keeps the automatic default with closed stdin"

run_with_closed_stdin custom '2\n0\n1\n2\n0,1,2\nY\n' \
    'SUCCESS: Custom configuration applied.' \
    'LLAMA_GPUS=GPU-0000,GPU-1111,GPU-2222' \
    'WHISPER_GPUS=GPU-0000' \
    'COMFYUI_GPUS=GPU-1111' \
    'EMBEDDINGS_GPUS=GPU-2222'
pass "custom assignment completes with closed stdin"

run_with_closed_stdin custom-empty-llama-default '2\n0\n1\n2\n\nY\n' \
    'SUCCESS: Custom configuration applied.' \
    'LLAMA_GPUS=GPU-0000,GPU-1111,GPU-2222'
pass "custom assignment keeps llama-server assigned when auxiliary services use every GPU"

run_with_closed_stdin custom-cancel '2\n0\n1\n2\n0,1,2\nn\n' \
    'WARN: Custom assignment cancelled; using automatic assignment.' \
    'SUCCESS: Assignment complete'
pass "cancelled custom assignment falls back to a valid automatic assignment"
