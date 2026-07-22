#!/usr/bin/env bash
# Regression: macOS `ods start` / `ods restart` must retry a failed detached
# full-model bootstrap upgrade from the persisted resume metadata.

set -euo pipefail

if (( BASH_VERSINFO[0] < 4 )); then
    for modern_bash in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        if [[ -x "$modern_bash" ]]; then
            exec "$modern_bash" "$0" "$@"
        fi
    done
    printf '[SKIP] ods-macos.sh requires Bash 4+\n'
    exit 0
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root_dir="$(cd "$script_dir/.." && pwd)"
macos_cli="$root_dir/installers/macos/ods-macos.sh"
tmp_dir="$(mktemp -d)"
install_dir="$tmp_dir/install"
bin_dir="$tmp_dir/bin"
resume_log="$tmp_dir/resume.log"
docker_log="$tmp_dir/docker.log"
trap 'rm -rf "$tmp_dir"' EXIT

mkdir -p "$install_dir/data/models" "$install_dir/scripts" "$install_dir/bin" "$bin_dir"
cp "$root_dir/docker-compose.base.yml" "$install_dir/docker-compose.base.yml"
printf '%s\n' '-f docker-compose.base.yml' > "$install_dir/.compose-flags"
touch "$install_dir/data/models/Qwen3.5-2B-Q4_K_M.gguf"
touch "$install_dir/bin/llama-server"
chmod +x "$install_dir/bin/llama-server"

cat > "$install_dir/.env" <<'ENV'
ODS_VERSION=2.5.3
ODS_MODE=local
GPU_BACKEND=apple
GPU_COUNT=1
TIER=1
BIND_ADDRESS=127.0.0.1
ODS_NATIVE_LLAMA_PORT=8080
GGUF_FILE=Qwen3.5-2B-Q4_K_M.gguf
CTX_SIZE=65536
MAX_CONTEXT=65536
SHIELD_API_KEY=test-shield-key
LLAMA_CPU_LIMIT=8.0
LLAMA_CPU_RESERVATION=2.0
TTS_CPU_LIMIT=8.0
TTS_CPU_RESERVATION=2.0
WHISPER_CPU_LIMIT=4.0
WHISPER_CPU_RESERVATION=1.0
HERMES_CPU_LIMIT=4.0
HERMES_CPU_RESERVATION=0.5
COMFYUI_CPU_LIMIT=16.0
COMFYUI_CPU_RESERVATION=2.0
ENV

cat > "$install_dir/data/bootstrap-status.json" <<'JSON'
{"status":"failed","model":"Qwen3.6-35B-A3B-UD-Q4_K_M.gguf","updatedAt":"2026-07-13T05:21:50Z"}
JSON
cat > "$install_dir/data/bootstrap-upgrade.args" <<'ARGS'
Qwen3.6-35B-A3B-UD-Q4_K_M.gguf
https://example.invalid/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf
sha-placeholder
Qwen 3.6 35B-A3B
131072
Qwen3.5-2B-Q4_K_M.gguf
ARGS

cat > "$install_dir/scripts/bootstrap-upgrade.sh" <<'UPGRADE'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${TEST_RESUME_LOG:?}"
UPGRADE
chmod +x "$install_dir/scripts/bootstrap-upgrade.sh"

cat > "$bin_dir/docker" <<'DOCKER'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "${TEST_DOCKER_LOG:?}"
if [[ "${1:-}" == "info" ]]; then
    [[ "$*" == *NCPU* ]] && printf '16\n'
    exit 0
fi
if [[ "${1:-}" == "compose" ]]; then
    shift
    joined=" $* "
    [[ "$joined" == *" up "* ]] && exit 0
    [[ "$joined" == *" ps "* ]] && { printf 'ods-dashboard Up 10 seconds 127.0.0.1:3001->3001/tcp\n'; exit 0; }
fi
if [[ "${1:-}" == "ps" ]]; then
    exit 0
fi
exit 0
DOCKER

cat > "$bin_dir/curl" <<'CURL'
#!/usr/bin/env bash
printf '200\n'
CURL

cat > "$bin_dir/pgrep" <<'PGREP'
#!/usr/bin/env bash
exit 1
PGREP

chmod +x "$bin_dir/docker" "$bin_dir/curl" "$bin_dir/pgrep"
: > "$docker_log"
: > "$resume_log"

run_cli() {
    local command="$1"
    PATH="$bin_dir:$PATH" \
    ODS_HOME="$install_dir" \
    NO_COLOR=1 \
    PYTHON_CMD="${PYTHON_CMD:-$(command -v python3 || command -v python)}" \
    TEST_DOCKER_LOG="$docker_log" \
    TEST_RESUME_LOG="$resume_log" \
        "$BASH" "$macos_cli" "$command" > "$tmp_dir/${command}.out" 2>&1 || {
            cat "$tmp_dir/${command}.out" >&2
            exit 1
        }
    for _ in {1..20}; do
        [[ -s "$resume_log" ]] && return 0
        /usr/bin/sleep 0.1
    done
    cat "$tmp_dir/${command}.out" >&2
    cat "$install_dir/logs/model-upgrade.log" >&2 2>/dev/null || true
    cat "$install_dir/data/bootstrap-upgrade.pid" >&2 2>/dev/null || true
    printf '[FAIL] macOS %s did not relaunch failed bootstrap-upgrade\n' "$command" >&2
    exit 1
}

run_cli start
grep -q 'Qwen3.6-35B-A3B-UD-Q4_K_M.gguf' "$resume_log" || {
    cat "$resume_log" >&2
    printf '[FAIL] macOS start did not pass full-model retry args\n' >&2
    exit 1
}
grep -q 'Qwen3.5-2B-Q4_K_M.gguf' "$resume_log" || {
    cat "$resume_log" >&2
    printf '[FAIL] macOS start did not pass bootstrap GGUF retry arg\n' >&2
    exit 1
}
grep -q 'Model upgrade failed previously; retrying in background' "$tmp_dir/start.out" || {
    cat "$tmp_dir/start.out" >&2
    printf '[FAIL] macOS start did not tell the operator it resumed the model upgrade\n' >&2
    exit 1
}

: > "$resume_log"
run_cli restart
grep -q 'Qwen3.6-35B-A3B-UD-Q4_K_M.gguf' "$resume_log" || {
    cat "$resume_log" >&2
    printf '[FAIL] macOS restart did not pass full-model retry args\n' >&2
    exit 1
}

: > "$resume_log"
run_cli update
grep -q 'Qwen3.6-35B-A3B-UD-Q4_K_M.gguf' "$resume_log" || {
    cat "$resume_log" >&2
    printf '[FAIL] macOS update did not pass full-model retry args\n' >&2
    exit 1
}
grep -q -- 'up -d' "$docker_log" || {
    cat "$docker_log" >&2
    printf '[FAIL] macOS start/restart/update did not bring compose services up\n' >&2
    exit 1
}

printf '[PASS] macOS start/restart/update retry failed bootstrap model upgrades\n'
