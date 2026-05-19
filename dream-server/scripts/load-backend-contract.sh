#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKEND_ID=""
ENV_MODE="false"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --backend)
            BACKEND_ID="${2:-}"
            shift 2
            ;;
        --env)
            ENV_MODE="true"
            shift
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 1
            ;;
    esac
done

if [[ -z "$BACKEND_ID" ]]; then
    echo "Missing required argument: --backend" >&2
    exit 1
fi

CONTRACT_FILE="${ROOT_DIR}/config/backends/${BACKEND_ID}.json"
if [[ ! -f "$CONTRACT_FILE" ]]; then
    echo "Backend contract not found: $CONTRACT_FILE" >&2
    exit 1
fi

if [[ "$ENV_MODE" == "true" ]]; then
    PYTHON_CMD="python3"
    if [[ -f "$ROOT_DIR/lib/python-cmd.sh" ]]; then
        . "$ROOT_DIR/lib/python-cmd.sh"
        PYTHON_CMD="$(ds_detect_python_cmd)"
    elif command -v python >/dev/null 2>&1; then
        PYTHON_CMD="python"
    fi

    "$PYTHON_CMD" - "$CONTRACT_FILE" <<'PY'
import json
import sys

contract = json.load(open(sys.argv[1], "r", encoding="utf-8"))

def out(key, value):
    safe = str(value).replace("\\", "\\\\").replace('"', '\\"')
    print(f'{key}="{safe}"')

out("BACKEND_CONTRACT_ID", contract.get("id", ""))
out("BACKEND_LLM_ENGINE", contract.get("llm_engine", ""))
out("BACKEND_SERVICE_NAME", contract.get("service_name", ""))
out("BACKEND_PUBLIC_API_PORT", contract.get("public_api_port", ""))
out("BACKEND_PUBLIC_HEALTH_URL", contract.get("public_health_url", ""))
out("BACKEND_PROVIDER_NAME", contract.get("provider_name", ""))
out("BACKEND_PROVIDER_URL", contract.get("provider_url", ""))
out("BACKEND_CONTRACT_FILE", sys.argv[1])

runtime = contract.get("runtime", {})
lemonade = runtime.get("lemonade", {}) if isinstance(runtime, dict) else {}
out("BACKEND_LEMONADE_CONTAINER_IMAGE", lemonade.get("container_image", ""))
out("BACKEND_LEMONADE_WINDOWS_VERSION", lemonade.get("windows_version", ""))
out("BACKEND_LEMONADE_WINDOWS_MSI_FILE", lemonade.get("windows_msi_file", ""))
out("BACKEND_LEMONADE_WINDOWS_EXECUTABLE", lemonade.get("windows_executable", ""))
out("BACKEND_LEMONADE_API_PORT", lemonade.get("api_port", ""))
out("BACKEND_LEMONADE_HEALTH_PATH", lemonade.get("health_path", ""))
out("BACKEND_LEMONADE_LINUX_BACKEND", lemonade.get("linux_backend", ""))
out("BACKEND_LEMONADE_WINDOWS_BACKEND", lemonade.get("windows_backend", ""))
PY
else
    cat "$CONTRACT_FILE"
fi
