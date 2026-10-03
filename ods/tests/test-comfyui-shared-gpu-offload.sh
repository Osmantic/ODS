#!/usr/bin/env bash
# Exercise the real ComfyUI entrypoint without a GPU or product mounts.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
startup="$root/extensions/services/comfyui/startup.sh"
compose="$root/extensions/services/comfyui/compose.nvidia.yaml"
standalone="$root/extensions/services/comfyui/compose.standalone.nvidia.yaml"
temp_base="$(cd "${TMPDIR:-/tmp}" && pwd -P)"
tmp="$(mktemp -d "$temp_base/ods-comfy-offload.XXXXXX")"
cleanup() {
    local resolved
    resolved="$(cd "$tmp" && pwd -P)" || return
    case "$resolved" in
        "$temp_base"/ods-comfy-offload.*) rm -rf -- "$resolved" ;;
        *) echo "Refusing to remove unexpected test path: $resolved" >&2; return 1 ;;
    esac
}
trap cleanup EXIT

mkdir -p "$tmp/comfy/models" "$tmp/models" "$tmp/input" "$tmp/output" "$tmp/bin"
: > "$tmp/empty.env"
sed \
    -e "s|^COMFYUI_DIR=.*|COMFYUI_DIR=\"$tmp/comfy\"|" \
    -e "s|^MODELS_MOUNT=.*|MODELS_MOUNT=\"$tmp/models\"|" \
    -e "s|^OUTPUT_MOUNT=.*|OUTPUT_MOUNT=\"$tmp/output\"|" \
    -e "s|^INPUT_MOUNT=.*|INPUT_MOUNT=\"$tmp/input\"|" \
    -e "s|^WORKFLOWS_MOUNT=.*|WORKFLOWS_MOUNT=\"$tmp/absent-workflows\"|" \
    -e "s|^USER_MOUNT=.*|USER_MOUNT=\"$tmp/absent-user\"|" \
    "$startup" > "$tmp/startup.sh"
cat > "$tmp/bin/python3" <<'PYTHON'
#!/usr/bin/env bash
if [[ "${1:-}" == -c ]]; then exit 0; fi
printf '%s\n' "$@" > "$COMFY_TEST_ARGS"
PYTHON
chmod +x "$tmp/bin/python3"

run_startup() {
    local value="$1"
    rm -f -- "$tmp/args"
    if [[ "$value" == UNSET ]]; then
        env -u COMFYUI_DISABLE_SMART_MEMORY PATH="$tmp/bin:$PATH" \
            COMFY_TEST_ARGS="$tmp/args" bash "$tmp/startup.sh" > "$tmp/stdout" 2> "$tmp/stderr"
    else
        env COMFYUI_DISABLE_SMART_MEMORY="$value" PATH="$tmp/bin:$PATH" \
            COMFY_TEST_ARGS="$tmp/args" bash "$tmp/startup.sh" > "$tmp/stdout" 2> "$tmp/stderr"
    fi
}

run_startup UNSET
! grep -qFx -- '--disable-smart-memory' "$tmp/args"
run_startup false
! grep -qFx -- '--disable-smart-memory' "$tmp/args"
run_startup true
[[ "$(grep -cFx -- '--disable-smart-memory' "$tmp/args")" == 1 ]]
if run_startup invalid; then
    echo 'Invalid ComfyUI memory setting must fail closed' >&2
    exit 1
fi
[[ ! -f "$tmp/args" ]]
grep -qF 'must be true or false' "$tmp/stderr"

python3 - "$root/.env.schema.json" <<'PYTHON'
import json
import sys
schema = json.load(open(sys.argv[1], encoding="utf-8"))
assert schema["properties"]["COMFYUI_DISABLE_SMART_MEMORY"]["enum"] == ["true", "false"]
PYTHON

env -u COMFYUI_DISABLE_SMART_MEMORY docker compose --env-file "$tmp/empty.env" -f "$compose" config --format json \
    | python3 -c 'import json,sys; assert json.load(sys.stdin)["services"]["comfyui"]["environment"]["COMFYUI_DISABLE_SMART_MEMORY"] == "true"'
COMFYUI_DISABLE_SMART_MEMORY=false docker compose --env-file "$tmp/empty.env" -f "$compose" config --format json \
    | python3 -c 'import json,sys; assert json.load(sys.stdin)["services"]["comfyui"]["environment"]["COMFYUI_DISABLE_SMART_MEMORY"] == "false"'
ODS_COMFYUI_DATA_ROOT="$tmp" ODS_COMFYUI_PORT=8189 COMFYUI_DISABLE_SMART_MEMORY=true \
    docker compose --env-file "$tmp/empty.env" -f "$standalone" config --format json \
    | python3 -c 'import json,sys; assert "COMFYUI_DISABLE_SMART_MEMORY" not in json.load(sys.stdin)["services"]["comfyui"].get("environment", {})'
echo 'PASS: full NVIDIA ComfyUI offloads by default; standalone remains unchanged'
