#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ODS_CLI="$ROOT_DIR/ods-cli"

FIXTURE=$(mktemp -d /tmp/test-gpu-reassign-manual.XXXXXX)
FAKE_INSTALL="$FIXTURE/install"
STUB_BIN="$FIXTURE/stubs"
mkdir -p "$FAKE_INSTALL" "$STUB_BIN"
trap 'rm -rf "$FIXTURE"' EXIT

: > "$FAKE_INSTALL/docker-compose.base.yml"
cat > "$FAKE_INSTALL/.env" <<'EOF'
GPU_BACKEND=nvidia
GPU_COUNT=3
GPU_ASSIGNMENT_JSON_B64=eyJncHVfYXNzaWdubWVudCI6eyJ2ZXJzaW9uIjoiMS4wIiwic3RyYXRlZ3kiOiJjb2xvY2F0ZWQiLCJzZXJ2aWNlcyI6eyJsbGFtYV9zZXJ2ZXIiOnsiZ3B1cyI6WyJHUFUtdGktMCIsIkdQVS0xMDgwIl0sImdwdV9pbmRpY2VzIjpbMCwxXSwicGFyYWxsZWxpc20iOnsibW9kZSI6InBpcGVsaW5lIiwidGVuc29yX3BhcmFsbGVsX3NpemUiOjEsInBpcGVsaW5lX3BhcmFsbGVsX3NpemUiOjIsImdwdV9tZW1vcnlfdXRpbGl6YXRpb24iOjAuOTV9fSwid2hpc3BlciI6eyJncHVzIjpbIkdQVS10aS0yIl0sImdwdV9pbmRpY2VzIjpbMl19LCJjb21meXVpIjp7ImdwdXMiOlsiR1BVLXRpLTIiXSwiZ3B1X2luZGljZXMiOlsyXX0sImVtYmVkZGluZ3MiOnsiZ3B1cyI6WyJHUFUtdGktMiJdLCJncHVfaW5kaWNlcyI6WzJdfX19fQ==
LLAMA_SERVER_GPU_UUIDS=GPU-ti-0,GPU-1080
LLAMA_SERVER_GPU_INDICES=0,1
LLAMA_ARG_SPLIT_MODE=layer
LLAMA_ARG_TENSOR_SPLIT=
WHISPER_GPU_UUID=GPU-ti-2
COMFYUI_GPU_UUID=GPU-ti-2
EMBEDDINGS_GPU_UUID=GPU-ti-2
LLM_MODEL_SIZE_MB=16000
ENABLED_SERVICES=llama_server,whisper,comfyui,embeddings
OLLAMA_PORT=12345
LLAMA_SERVER_PORT=54321
EOF

cat > "$STUB_BIN/nvidia-smi" <<'STUB'
#!/usr/bin/env bash
case "$*" in
    *"--query-gpu=index,name,memory.total,memory.free,pcie.link.gen.current,pcie.link.width.current,uuid"*)
        printf '%s\n' \
            "0, NVIDIA GeForce GTX 1080 Ti, 11264, 10240, 3, 16, GPU-ti-0" \
            "1, NVIDIA GeForce GTX 1080, 8192, 7168, 3, 16, GPU-1080" \
            "2, NVIDIA GeForce GTX 1080 Ti, 11264, 9216, 3, 16, GPU-ti-2"
        ;;
    *"--query-gpu=index,name,memory.total"*)
        printf '%s\n' \
            "0, NVIDIA GeForce GTX 1080 Ti, 11264" \
            "1, NVIDIA GeForce GTX 1080, 8192" \
            "2, NVIDIA GeForce GTX 1080 Ti, 11264"
        ;;
    *"--query-gpu=index,uuid"*)
        printf '%s\n' "0, GPU-ti-0" "1, GPU-1080" "2, GPU-ti-2"
        ;;
    *"--query-gpu=uuid"*)
        printf '%s\n' "GPU-ti-0" "GPU-1080" "GPU-ti-2"
        ;;
    *"--query-gpu=driver_version"*) echo "580.0" ;;
    "--list-gpus")
        printf '%s\n' \
            "GPU 0: NVIDIA GeForce GTX 1080 Ti (UUID: GPU-ti-0)" \
            "GPU 1: NVIDIA GeForce GTX 1080 (UUID: GPU-1080)" \
            "GPU 2: NVIDIA GeForce GTX 1080 Ti (UUID: GPU-ti-2)"
        ;;
    "topo -m")
        cat <<'EOF'
        GPU0    GPU1    GPU2
GPU0     X      PHB     PHB
GPU1    PHB      X      PHB
GPU2    PHB     PHB      X
EOF
        ;;
    "-q")
        if [[ "${NVIDIA_MIG_ENABLED:-0}" == "1" ]]; then
            echo "MIG Mode : Enabled"
        else
            echo "MIG Mode : Disabled"
        fi
        ;;
    *) exit 1 ;;
esac
STUB

cat > "$STUB_BIN/docker" <<'STUB'
#!/usr/bin/env bash
case "$*" in
    compose\ *\ up\ -d\ --force-recreate)
        if [[ -n "${DOCKER_TRACE_FILE:-}" ]]; then
            printf '%s\n' "${LLAMA_SERVER_GPU_UUIDS:-unset}" >> "$DOCKER_TRACE_FILE"
        fi
        if [[ -n "${DOCKER_FAIL_ONCE_FILE:-}" && ! -f "$DOCKER_FAIL_ONCE_FILE" ]]; then
            : > "$DOCKER_FAIL_ONCE_FILE"
            exit 1
        fi
        ;;
    compose\ *\ config\ --services)
        printf '%s\n' dashboard-api llama-server
        ;;
    compose\ *\ ps\ -q\ dashboard-api)
        echo dashboard-container
        ;;
    compose\ *\ ps\ -q\ llama-server)
        echo llama-container
        ;;
    "inspect "*)
        if [[ "${DOCKER_UNHEALTHY_ASSIGNMENT:-}" == "${LLAMA_SERVER_GPU_UUIDS:-}" ]]; then
            echo "running unhealthy"
        elif [[ "${DOCKER_EXIT_ASSIGNMENT:-}" == "${LLAMA_SERVER_GPU_UUIDS:-}" ]]; then
            echo "exited"
        else
            echo "running healthy"
        fi
        ;;
esac
STUB

cat > "$STUB_BIN/curl" <<'STUB'
#!/usr/bin/env bash
if [[ -n "${CURL_TRACE_FILE:-}" ]]; then
    printf '%s\n' "$*" >> "$CURL_TRACE_FILE"
fi
if [[ "${CURL_FAIL_ASSIGNMENT:-}" == "${LLAMA_SERVER_GPU_UUIDS:-}" ]]; then
    exit 22
fi
case "$*" in
    *"/v1/models"*) echo '{"data":[{"id":"test-model"}]}' ;;
    *"/v1/chat/completions"*) echo '{"choices":[{"message":{"content":"OK"}}]}' ;;
    *) echo '{"status":"ok"}' ;;
esac
STUB

chmod +x "$STUB_BIN"/*
STUB_PATH="$STUB_BIN:$PATH"
export CURL_TRACE_FILE="$FIXTURE/curl-trace"
: > "$CURL_TRACE_FILE"

run_manual() {
    local input="$1"
    set +e
    OUTPUT=$(printf '%s' "$input" |
        ODS_HOME="$FAKE_INSTALL" PATH="$STUB_PATH" \
        ODS_GPU_REASSIGN_HEALTH_ATTEMPTS=1 \
        ODS_GPU_REASSIGN_HEALTH_INTERVAL_SECONDS=0 \
        "$ODS_CLI" gpu reassign --manual 2>&1)
    RC=$?
    set -e
}

run_cli() {
    set +e
    OUTPUT=$(ODS_HOME="$FAKE_INSTALL" PATH="$STUB_PATH" \
        ODS_GPU_REASSIGN_HEALTH_ATTEMPTS=1 \
        ODS_GPU_REASSIGN_HEALTH_INTERVAL_SECONDS=0 \
        "$ODS_CLI" "$@" 2>&1)
    RC=$?
    set -e
}

env_value() {
    local key="$1"
    awk -F= -v key="$key" 'index($0, key "=") == 1 { print substr($0, length(key) + 2) }' \
        "$FAKE_INSTALL/.env"
}

run_manual $'0,1,2\n2\n\n1\npipeline\nn\n'
[[ $RC -eq 0 ]] || { echo "[FAIL] manual reassignment failed: $OUTPUT"; exit 1; }

assignment=$(env_value GPU_ASSIGNMENT_JSON_B64 | base64 -d)
echo "$assignment" | jq -e '
    .gpu_assignment.version == "1.0"
    and .gpu_assignment.strategy == "manual"
    and .gpu_assignment.services.llama_server.gpus
        == ["GPU-ti-0", "GPU-1080", "GPU-ti-2"]
    and .gpu_assignment.services.llama_server.gpu_indices == [0, 1, 2]
    and .gpu_assignment.services.llama_server.parallelism.mode == "pipeline"
    and .gpu_assignment.services.llama_server.parallelism.tensor_parallel_size == 1
    and .gpu_assignment.services.llama_server.parallelism.pipeline_parallel_size == 3
    and .gpu_assignment.services.whisper.gpus == ["GPU-ti-2"]
    and .gpu_assignment.services.comfyui.gpus == ["GPU-ti-2"]
    and .gpu_assignment.services.embeddings.gpus == ["GPU-1080"]
' >/dev/null

[[ "$(env_value LLAMA_SERVER_GPU_UUIDS)" == "GPU-ti-0,GPU-1080,GPU-ti-2" ]]
[[ "$(env_value LLAMA_SERVER_GPU_INDICES)" == "0,1,2" ]]
[[ "$(env_value LLAMA_ARG_SPLIT_MODE)" == "layer" ]]
[[ -z "$(env_value LLAMA_ARG_TENSOR_SPLIT)" ]]
[[ "$(env_value WHISPER_GPU_UUID)" == "GPU-ti-2" ]]
[[ "$(env_value COMFYUI_GPU_UUID)" == "GPU-ti-2" ]]
[[ "$(env_value EMBEDDINGS_GPU_UUID)" == "GPU-1080" ]]

run_manual $'0,2\n1\n2\n\n\nn\n'
[[ $RC -eq 0 ]] || { echo "[FAIL] tensor reassignment failed: $OUTPUT"; exit 1; }

assignment=$(env_value GPU_ASSIGNMENT_JSON_B64 | base64 -d)
echo "$assignment" | jq -e '
    .gpu_assignment.services.llama_server.gpus == ["GPU-ti-0", "GPU-ti-2"]
    and .gpu_assignment.services.llama_server.parallelism == {
        mode: "tensor",
        tensor_parallel_size: 2,
        pipeline_parallel_size: 1,
        gpu_memory_utilization: 0.93,
        tensor_split: [1, 1]
    }
    and .gpu_assignment.services.whisper.gpus == ["GPU-1080"]
    and .gpu_assignment.services.comfyui.gpus == ["GPU-ti-2"]
    and .gpu_assignment.services.embeddings.gpus == ["GPU-1080"]
' >/dev/null
[[ "$(env_value LLAMA_ARG_SPLIT_MODE)" == "row" ]]
[[ "$(env_value LLAMA_ARG_TENSOR_SPLIT)" == "1,1" ]]
[[ "$(env_value EMBEDDINGS_GPU_UUID)" == "GPU-1080" ]]

run_cli gpu assignment
[[ $RC -eq 0 ]]
echo "$OUTPUT" | grep -q "Strategy: manual"
echo "$OUTPUT" | grep -q "llama_server.*GPU0, GPU2.*tensor"

run_cli gpu validate
[[ $RC -eq 0 ]]
echo "$OUTPUT" | grep -q "Result: 3 check(s) passed, 0 failed"

before_hash=$(sha256sum "$FAKE_INSTALL/.env" | awk '{print $1}')
run_manual $'0,3\n\n\n\n'
[[ $RC -ne 0 ]] || { echo "[FAIL] nonexistent GPU index was accepted"; exit 1; }
echo "$OUTPUT" | grep -q "Invalid llama-server GPU list"
after_hash=$(sha256sum "$FAKE_INSTALL/.env" | awk '{print $1}')
[[ "$before_hash" == "$after_hash" ]] || {
    echo "[FAIL] invalid manual input mutated .env"
    exit 1
}

before_hash=$(sha256sum "$FAKE_INSTALL/.env" | awk '{print $1}')
run_manual $'0,1\n0,2\n\n\n'
[[ $RC -ne 0 ]] || { echo "[FAIL] multiple auxiliary GPU indices were accepted"; exit 1; }
echo "$OUTPUT" | grep -q "Invalid Whisper GPU index"
after_hash=$(sha256sum "$FAKE_INSTALL/.env" | awk '{print $1}')
[[ "$before_hash" == "$after_hash" ]] || {
    echo "[FAIL] invalid auxiliary input mutated .env"
    exit 1
}

before_hash=$(sha256sum "$FAKE_INSTALL/.env" | awk '{print $1}')
export DOCKER_TRACE_FILE="$FIXTURE/docker-trace"
export DOCKER_FAIL_ONCE_FILE="$FIXTURE/docker-fail-once"
run_manual $'0,1,2\n2\n2\n1\npipeline\ny\n'
[[ $RC -ne 0 ]] || { echo "[FAIL] failed apply reported success"; exit 1; }
echo "$OUTPUT" | grep -q "previous configuration and containers were restored"
after_hash=$(sha256sum "$FAKE_INSTALL/.env" | awk '{print $1}')
[[ "$before_hash" == "$after_hash" ]] || {
    echo "[FAIL] failed apply did not restore .env"
    exit 1
}
mapfile -t docker_assignments < "$DOCKER_TRACE_FILE"
[[ "${#docker_assignments[@]}" -eq 2 ]]
[[ "${docker_assignments[0]}" == "GPU-ti-0,GPU-1080,GPU-ti-2" ]]
[[ "${docker_assignments[1]}" == "GPU-ti-0,GPU-ti-2" ]]
unset DOCKER_TRACE_FILE DOCKER_FAIL_ONCE_FILE

export DOCKER_TRACE_FILE="$FIXTURE/docker-success-trace"
run_manual $'0,1,2\n2\n2\n1\npipeline\ny\n'
[[ $RC -eq 0 ]] || { echo "[FAIL] successful apply failed: $OUTPUT"; exit 1; }
mapfile -t docker_assignments < "$DOCKER_TRACE_FILE"
[[ "${#docker_assignments[@]}" -eq 1 ]]
[[ "${docker_assignments[0]}" == "GPU-ti-0,GPU-1080,GPU-ti-2" ]]
unset DOCKER_TRACE_FILE
grep -q "127.0.0.1:12345/v1/models" "$CURL_TRACE_FILE"
if grep -q "127.0.0.1:54321/" "$CURL_TRACE_FILE"; then
    echo "[FAIL] readiness preferred deprecated LLAMA_SERVER_PORT over OLLAMA_PORT"
    exit 1
fi

before_hash=$(sha256sum "$FAKE_INSTALL/.env" | awk '{print $1}')
export DOCKER_TRACE_FILE="$FIXTURE/docker-unhealthy-trace"
export DOCKER_UNHEALTHY_ASSIGNMENT="GPU-ti-0,GPU-ti-2"
run_manual $'0,2\n2\n2\n1\ntensor\ny\n'
[[ $RC -ne 0 ]] || { echo "[FAIL] unhealthy recreated stack reported success"; exit 1; }
echo "$OUTPUT" | grep -q "previous configuration and containers were restored"
after_hash=$(sha256sum "$FAKE_INSTALL/.env" | awk '{print $1}')
[[ "$before_hash" == "$after_hash" ]] || {
    echo "[FAIL] unhealthy apply did not restore .env"
    exit 1
}
mapfile -t docker_assignments < "$DOCKER_TRACE_FILE"
[[ "${#docker_assignments[@]}" -eq 2 ]]
[[ "${docker_assignments[0]}" == "GPU-ti-0,GPU-ti-2" ]]
[[ "${docker_assignments[1]}" == "GPU-ti-0,GPU-1080,GPU-ti-2" ]]
unset DOCKER_TRACE_FILE DOCKER_UNHEALTHY_ASSIGNMENT

before_hash=$(sha256sum "$FAKE_INSTALL/.env" | awk '{print $1}')
export DOCKER_TRACE_FILE="$FIXTURE/docker-exit-trace"
export DOCKER_EXIT_ASSIGNMENT="GPU-ti-0,GPU-ti-2"
run_manual $'0,2\n2\n2\n1\ntensor\ny\n'
[[ $RC -ne 0 ]] || { echo "[FAIL] exited recreated stack reported success"; exit 1; }
echo "$OUTPUT" | grep -q "previous configuration and containers were restored"
after_hash=$(sha256sum "$FAKE_INSTALL/.env" | awk '{print $1}')
[[ "$before_hash" == "$after_hash" ]] || {
    echo "[FAIL] exited apply did not restore .env"
    exit 1
}
unset DOCKER_TRACE_FILE DOCKER_EXIT_ASSIGNMENT

before_hash=$(sha256sum "$FAKE_INSTALL/.env" | awk '{print $1}')
export NVIDIA_MIG_ENABLED=1
set +e
OUTPUT=$(ODS_HOME="$FAKE_INSTALL" PATH="$STUB_PATH" "$ODS_CLI" \
    gpu reassign --auto 2>&1)
RC=$?
set -e
[[ $RC -ne 0 ]] || { echo "[FAIL] physical-GPU MIG topology was accepted"; exit 1; }
echo "$OUTPUT" | grep -q "assignable MIG instances"
after_hash=$(sha256sum "$FAKE_INSTALL/.env" | awk '{print $1}')
[[ "$before_hash" == "$after_hash" ]] || {
    echo "[FAIL] MIG rejection mutated .env"
    exit 1
}
unset NVIDIA_MIG_ENABLED

cat > "$FAKE_INSTALL/.env" <<'EOF'
GPU_BACKEND=nvidia
GPU_COUNT=3
GPU_ASSIGNMENT_JSON_B64=
LLAMA_SERVER_GPU_UUIDS=GPU-ti-0,GPU-1080
LLAMA_SERVER_GPU_INDICES=0,1
LLAMA_ARG_SPLIT_MODE=layer
LLAMA_ARG_TENSOR_SPLIT=
WHISPER_GPU_UUID=
COMFYUI_GPU_UUID=
EMBEDDINGS_GPU_UUID=
LLM_MODEL_SIZE_MB=16000
ENABLED_SERVICES=llama_server,whisper
EOF
run_manual $'0,1\n\n\n\npipeline\nn\n'
[[ $RC -eq 0 ]] || { echo "[FAIL] legacy empty assignment failed: $OUTPUT"; exit 1; }
assignment=$(env_value GPU_ASSIGNMENT_JSON_B64 | base64 -d)
echo "$assignment" | jq -e '
    .gpu_assignment.services.llama_server.gpus == ["GPU-ti-0", "GPU-1080"]
    and .gpu_assignment.services.whisper.gpus == ["GPU-ti-0"]
    and (.gpu_assignment.services | has("comfyui") | not)
    and (.gpu_assignment.services | has("embeddings") | not)
' >/dev/null
[[ "$(env_value WHISPER_GPU_UUID)" == "GPU-ti-0" ]]
[[ -z "$(env_value COMFYUI_GPU_UUID)" ]]
[[ -z "$(env_value EMBEDDINGS_GPU_UUID)" ]]

sed -i \
    -e 's/^LLAMA_ARG_TENSOR_SPLIT=.*/LLAMA_ARG_TENSOR_SPLIT=9,9,9/' \
    -e 's/^COMFYUI_GPU_UUID=.*/COMFYUI_GPU_UUID=GPU-stale/' \
    -e 's/^EMBEDDINGS_GPU_UUID=.*/EMBEDDINGS_GPU_UUID=GPU-stale/' \
    "$FAKE_INSTALL/.env"
set +e
OUTPUT=$(printf 'n\n' |
    ODS_HOME="$FAKE_INSTALL" PATH="$STUB_PATH" "$ODS_CLI" gpu reassign --auto 2>&1)
RC=$?
set -e
[[ $RC -eq 0 ]] || { echo "[FAIL] automatic reassignment failed: $OUTPUT"; exit 1; }
assignment=$(env_value GPU_ASSIGNMENT_JSON_B64 | base64 -d)
expected_llama_uuids=$(echo "$assignment" | jq -r \
    '.gpu_assignment.services.llama_server.gpus | join(",")')
expected_llama_indices=$(echo "$assignment" | jq -r \
    '.gpu_assignment.services.llama_server.gpu_indices | map(tostring) | join(",")')
expected_tensor_split=$(echo "$assignment" | jq -r \
    '(.gpu_assignment.services.llama_server.parallelism.tensor_split // []) | map(tostring) | join(",")')
[[ "$(env_value LLAMA_SERVER_GPU_UUIDS)" == "$expected_llama_uuids" ]]
[[ "$(env_value LLAMA_SERVER_GPU_INDICES)" == "$expected_llama_indices" ]]
[[ "$(env_value LLAMA_ARG_TENSOR_SPLIT)" == "$expected_tensor_split" ]]
[[ -z "$(env_value COMFYUI_GPU_UUID)" ]]
[[ -z "$(env_value EMBEDDINGS_GPU_UUID)" ]]

AMD_ROOT="$FIXTURE/amd-root"
AMD_INSTALL="$FIXTURE/amd-install"
mkdir -p "$AMD_ROOT/installers/lib" "$AMD_ROOT/scripts" "$AMD_INSTALL"
cp "$ROOT_DIR/ods-cli" "$AMD_ROOT/ods-cli"
cp -R "$ROOT_DIR/lib" "$AMD_ROOT/lib"
cp "$ROOT_DIR/scripts/assign_gpus.py" "$AMD_ROOT/scripts/assign_gpus.py"
chmod +x "$AMD_ROOT/ods-cli"
: > "$AMD_INSTALL/docker-compose.base.yml"
cat > "$AMD_INSTALL/.env" <<'EOF'
GPU_BACKEND=amd
GPU_COUNT=3
GPU_ASSIGNMENT_JSON_B64=
LLAMA_SERVER_GPU_INDICES=0,1
WHISPER_GPU_INDEX=2
COMFYUI_GPU_INDEX=2
EMBEDDINGS_GPU_INDEX=2
LLM_MODEL_SIZE_MB=16000
ENABLED_SERVICES=llama_server,whisper,comfyui,embeddings
EOF
cat > "$AMD_ROOT/installers/lib/amd-topo.sh" <<'STUB'
detect_amd_topo() {
    cat <<'JSON'
{"vendor":"amd","gpu_count":3,"gpus":[
  {"index":0,"uuid":"GPU-amd-0","name":"AMD 0","memory_gb":16,"memory_free_gb":15,"memory_type":"discrete","gfx_version":"gfx1100","render_node":128},
  {"index":1,"uuid":"GPU-amd-1","name":"AMD 1","memory_gb":16,"memory_free_gb":15,"memory_type":"discrete","gfx_version":"gfx1100","render_node":129},
  {"index":2,"uuid":"GPU-amd-2","name":"AMD 2","memory_gb":16,"memory_free_gb":15,"memory_type":"discrete","gfx_version":"gfx1100","render_node":130}
],"links":[]}
JSON
}
STUB

set +e
AMD_OUTPUT=$(printf '0,1,2\n1\n0\n2\npipeline\nn\n' |
    ODS_HOME="$AMD_INSTALL" PATH="$STUB_PATH" "$AMD_ROOT/ods-cli" \
        gpu reassign --manual 2>&1)
AMD_RC=$?
set -e
[[ $AMD_RC -eq 0 ]] || {
    echo "[FAIL] AMD manual reassignment failed: $AMD_OUTPUT"
    exit 1
}
amd_env_value() {
    local key="$1"
    awk -F= -v key="$key" 'index($0, key "=") == 1 { print substr($0, length(key) + 2) }' \
        "$AMD_INSTALL/.env"
}
[[ "$(amd_env_value LLAMA_SERVER_GPU_INDICES)" == "0,1,2" ]]
[[ "$(amd_env_value ROCR_VISIBLE_DEVICES)" == "0,1,2" ]]
[[ "$(amd_env_value WHISPER_GPU_INDEX)" == "1" ]]
[[ "$(amd_env_value COMFYUI_GPU_INDEX)" == "0" ]]
[[ "$(amd_env_value EMBEDDINGS_GPU_INDEX)" == "2" ]]
amd_assignment=$(amd_env_value GPU_ASSIGNMENT_JSON_B64 | base64 -d)
echo "$amd_assignment" | jq -e '
    .gpu_assignment.strategy == "manual"
    and .gpu_assignment.services.llama_server.gpus
        == ["GPU-amd-0", "GPU-amd-1", "GPU-amd-2"]
    and .gpu_assignment.services.whisper.gpu_indices == [1]
    and .gpu_assignment.services.comfyui.gpu_indices == [0]
    and .gpu_assignment.services.embeddings.gpu_indices == [2]
' >/dev/null

echo "[PASS] manual GPU reassignment persists one validated assignment contract"
