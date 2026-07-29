#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="$(command -v python3 || command -v python)"
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/ods-cli-model.XXXXXX")"
trap 'rm -rf "$TMP_ROOT"' EXIT

INSTALL_DIR="$TMP_ROOT/install"
CAPTURE_DIR="$TMP_ROOT/capture"
FAKE_BIN="$TMP_ROOT/bin"
mkdir -p \
    "$INSTALL_DIR/installers/lib" \
    "$INSTALL_DIR/config" \
    "$INSTALL_DIR/data/models" \
    "$CAPTURE_DIR" \
    "$FAKE_BIN"

cp "$ROOT_DIR/installers/lib/tier-map.sh" "$INSTALL_DIR/installers/lib/tier-map.sh"
cp "$ROOT_DIR/config/model-library.json" "$INSTALL_DIR/config/model-library.json"
touch "$INSTALL_DIR/docker-compose.base.yml"
printf 'model\n' > "$INSTALL_DIR/data/models/Qwen3.5-9B-Q4_K_M.gguf"
cat > "$INSTALL_DIR/.env" <<'ENV'
ODS_MODE=local
ODS_AGENT_KEY=stale-agent-key
ODS_AGENT_PORT=7710
MODEL_PROFILE=qwen
GPU_BACKEND=nvidia
LLM_MODEL=old-model
GGUF_FILE=old-model.gguf
CTX_SIZE=2048
MAX_CONTEXT=2048
ODS_AGENT_KEY="test-agent-key"
ENV

cat > "$FAKE_BIN/curl" <<'FAKE_CURL'
#!/usr/bin/env bash
set -euo pipefail

is_health="false"
for argument in "$@"; do
    if [[ "$argument" == */health ]]; then
        is_health="true"
    fi
done

output_file=""
request_json=""
header_file=""
header_stdin="false"
request_url=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --output)
            output_file="$2"
            shift 2
            ;;
        --data)
            request_json="$2"
            shift 2
            ;;
        --header)
            if [[ "$2" == "@-" ]]; then
                header_stdin="true"
            elif [[ "$2" == @* ]]; then
                header_file="${2#@}"
            fi
            shift 2
            ;;
        *)
            if [[ "$1" == http://* ]]; then
                request_url="$1"
            fi
            shift
            ;;
    esac
done

printf '%s\n' "$request_url" >> "$CAPTURE_DIR/urls"
if [[ "$is_health" == "true" ]]; then
    exit 0
fi

printf '%s\n' "$request_json" > "$CAPTURE_DIR/request.json"
if [[ "$header_stdin" == "true" ]]; then
    cat > "$CAPTURE_DIR/auth-header"
else
    cp "$header_file" "$CAPTURE_DIR/auth-header"
fi
if [[ "${FAKE_CURL_TRANSPORT_FAIL:-false}" == "true" ]]; then
    exit 7
fi
if [[ "${FAKE_ACTIVATION_FAIL:-false}" == "true" ]]; then
    printf '{"error":"simulated rollback receipt","rolled_back":true}\n' > "$output_file"
    printf '500'
    exit 0
fi
printf '{"status":"activated","model_id":"qwen3.5-9b-q4","llm_model":"qwen3.5-9b","gguf_file":"Qwen3.5-9B-Q4_K_M.gguf","tier":"1","context_length":16384}\n' > "$output_file"
printf '200'
FAKE_CURL
chmod 700 "$FAKE_BIN/curl"

cat > "$FAKE_BIN/docker" <<'FAKE_DOCKER'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "network" && "${2:-}" == "inspect" ]]; then
    [[ -n "${FAKE_DOCKER_GATEWAY:-}" ]] || exit 1
    printf '%s\n' "$FAKE_DOCKER_GATEWAY"
    exit 0
fi
exit 1
FAKE_DOCKER
chmod 700 "$FAKE_BIN/docker"

if ! command -v jq >/dev/null 2>&1; then
    cat > "$FAKE_BIN/jq" <<'FAKE_JQ'
#!/usr/bin/env python
import json
import sys

args = sys.argv[1:]
if "-nc" in args:
    values = {}
    index = 0
    while index < len(args):
        if args[index] in {"--arg", "--argjson"}:
            kind, name, value = args[index:index + 3]
            values[name] = json.loads(value) if kind == "--argjson" else value
            index += 3
        else:
            index += 1
    print(json.dumps({
        "model_id": values["model_id"],
        "tier": values["tier"],
        "context_length": values["context_length"],
    }))
elif "--arg" in args and "file" in args:
    file_index = args.index("file")
    filename = args[file_index + 1]
    with open(args[-1], encoding="utf-8") as handle:
        library = json.load(handle)
    match = next(
        (model["id"] for model in library.get("models", [])
         if model.get("gguf_file") == filename),
        "",
    )
    print(match)
else:
    with open(args[-1], encoding="utf-8") as handle:
        payload = json.load(handle)
    expression = " ".join(args)
    if ".error" in expression:
        field = "error"
    elif ".context_length" in expression:
        field = "context_length"
    elif ".tier" in expression:
        field = "tier"
    else:
        field = "status"
    print(payload.get(field, ""))
FAKE_JQ
    chmod 700 "$FAKE_BIN/jq"
fi

output="$({
    ODS_HOME="$INSTALL_DIR" \
    CAPTURE_DIR="$CAPTURE_DIR" \
    FAKE_DOCKER_GATEWAY=172.31.0.1 \
    HOST_ARCH=amd64 \
    NO_COLOR=1 \
    PATH="$FAKE_BIN:$PATH" \
        "$ROOT_DIR/ods-cli" model swap T1
} 2>&1)"

grep -q 'Model activated everywhere: qwen3.5-9b' <<< "$output"
grep -qx 'http://172.31.0.1:7710/health' "$CAPTURE_DIR/urls"
grep -qx 'http://172.31.0.1:7710/v1/model/activate' "$CAPTURE_DIR/urls"
"$PYTHON_BIN" - "$CAPTURE_DIR/request.json" <<'VERIFY_REQUEST'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    request = json.load(handle)
assert request == {
    "model_id": "qwen3.5-9b-q4",
    "tier": "1",
    "context_length": 16384,
}
VERIFY_REQUEST
grep -qx 'Authorization: Bearer test-agent-key' "$CAPTURE_DIR/auth-header"

# The CLI must leave state mutation to the host-agent transaction.
grep -qx 'LLM_MODEL=old-model' "$INSTALL_DIR/.env"
grep -qx 'GGUF_FILE=old-model.gguf' "$INSTALL_DIR/.env"
grep -qx 'MAX_CONTEXT=2048' "$INSTALL_DIR/.env"

# Explicit quoted IPv6 binds must become a valid loopback URL rather than a
# wildcard or a value containing quote characters. The last duplicate value
# must win, matching the host-agent .env parser.
printf 'ODS_AGENT_BIND=192.0.2.10\n' >> "$INSTALL_DIR/.env"
printf 'ODS_AGENT_BIND="::1"\n' >> "$INSTALL_DIR/.env"
rm -f "$CAPTURE_DIR/urls"
ipv6_output="$({
    ODS_HOME="$INSTALL_DIR" \
    CAPTURE_DIR="$CAPTURE_DIR" \
    HOST_ARCH=amd64 \
    NO_COLOR=1 \
    PATH="$FAKE_BIN:$PATH" \
        "$ROOT_DIR/ods-cli" model swap T1
} 2>&1)"
grep -q 'Model activated everywhere: qwen3.5-9b' <<< "$ipv6_output"
grep -Fqx 'http://[::1]:7710/health' "$CAPTURE_DIR/urls"
grep -Fqx 'http://[::1]:7710/v1/model/activate' "$CAPTURE_DIR/urls"

set +e
failure_output="$({
    ODS_HOME="$INSTALL_DIR" \
    CAPTURE_DIR="$CAPTURE_DIR" \
    FAKE_ACTIVATION_FAIL=true \
    HOST_ARCH=amd64 \
    NO_COLOR=1 \
    PATH="$FAKE_BIN:$PATH" \
        "$ROOT_DIR/ods-cli" model swap T1
} 2>&1)"
failure_status=$?
set -e
[[ $failure_status -ne 0 ]]
grep -q 'Model activation failed (HTTP 500): simulated rollback receipt' \
    <<< "$failure_output"
grep -qx 'LLM_MODEL=old-model' "$INSTALL_DIR/.env"
grep -qx 'GGUF_FILE=old-model.gguf' "$INSTALL_DIR/.env"

set +e
transport_output="$({
    ODS_HOME="$INSTALL_DIR" \
    CAPTURE_DIR="$CAPTURE_DIR" \
    FAKE_CURL_TRANSPORT_FAIL=true \
    HOST_ARCH=amd64 \
    NO_COLOR=1 \
    PATH="$FAKE_BIN:$PATH" \
        "$ROOT_DIR/ods-cli" model swap T1
} 2>&1)"
transport_status=$?
set -e
[[ $transport_status -ne 0 ]]
grep -q 'Model activation request failed before the host agent completed' \
    <<< "$transport_output"
grep -qx 'LLM_MODEL=old-model' "$INSTALL_DIR/.env"

echo "[PASS] ods model swap uses transactional host-agent activation"
