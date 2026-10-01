#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
installer="$root/installers/macos/install-macos.sh"
cli="$root/installers/macos/ods-macos.sh"
scratch="$(mktemp -d)"
trap 'rm -rf -- "$scratch"' EXIT
INSTALL_DIR="$scratch/install"
mkdir -p "$INSTALL_DIR"

read_env_value() {
    local line
    line="$(grep -m1 "^${2}=" "$1" 2>/dev/null || true)"
    printf '%s\n' "${line#*=}"
}
ai_err() { printf '%s\n' "$*" >&2; }
ensure_hermes_dashboard_session_token() { :; }
macos_model_store_compose_flags() { printf '%s\n' "$1"; }
eval "$(sed -n '/^_macos_resolve_webui_selection() {/,/^}/p' "$installer")"
eval "$(sed -n '/^_get_base_compose_flags() {/,/^}/p' "$cli")"

ENABLE_OPEN_WEBUI=false WEBUI_RETAINED="" WEBUI_ENABLE_EXPLICIT=false
WEBUI_DISABLE_EXPLICIT=false ALL_FEATURES=false
_macos_resolve_webui_selection
[[ "$ENABLE_OPEN_WEBUI" == false ]] || { echo 'fresh Core selected WebUI' >&2; exit 1; }

printf 'ODS_MODE=local\n' > "$INSTALL_DIR/.env"
_macos_resolve_webui_selection
[[ "$ENABLE_OPEN_WEBUI" == true && "$WEBUI_RETAINED" == true ]] \
    || { echo 'older installed WebUI choice was lost' >&2; exit 1; }

printf 'ENABLE_OPEN_WEBUI=false\n' > "$INSTALL_DIR/.env"
_macos_resolve_webui_selection
[[ "$ENABLE_OPEN_WEBUI" == false && "$WEBUI_RETAINED" == false ]] \
    || { echo 'lean installed WebUI choice was lost' >&2; exit 1; }
WEBUI_ENABLE_EXPLICIT=true ENABLE_OPEN_WEBUI=true
_macos_resolve_webui_selection
[[ "$ENABLE_OPEN_WEBUI" == true ]] \
    || { echo 'explicit WebUI addback was ignored' >&2; exit 1; }

# A missing CLI Compose cache must pass the retained selection to the shared
# resolver; otherwise a lean installation silently reselects WebUI on update.
mkdir -p "$INSTALL_DIR/scripts"
cat > "$INSTALL_DIR/scripts/resolve-compose-stack.sh" <<'RESOLVER'
#!/usr/bin/env bash
printf '%s\n' "${ENABLE_OPEN_WEBUI:-missing}"
RESOLVER
chmod +x "$INSTALL_DIR/scripts/resolve-compose-stack.sh"
[[ "$(_get_base_compose_flags)" == false ]] \
    || { echo 'CLI fallback reselected WebUI for a lean install' >&2; exit 1; }
printf 'ODS_MODE=local\n' > "$INSTALL_DIR/.env"
[[ "$(_get_base_compose_flags)" == true ]] \
    || { echo 'CLI fallback dropped WebUI for an older install' >&2; exit 1; }

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    compose_base=(docker compose -f "$root/docker-compose.base.yml" \
        -f "$root/installers/macos/docker-compose.macos.yml")
    # Capture the complete output before grepping. With pipefail, grep -q can
    # close the pipe after finding WebUI and make Compose fail with SIGPIPE.
    base_services="$(WEBUI_SECRET=test-placeholder "${compose_base[@]}" config --services)" \
        || { echo 'Base macOS Compose config failed' >&2; exit 1; }
    grep -qx 'open-webui' <<< "$base_services" \
        || { echo 'WebUI option omitted its service' >&2; exit 1; }
    lean_services="$(WEBUI_SECRET=test-placeholder "${compose_base[@]}" \
        -f "$root/docker-compose.gateway-only.yml" config --services)"
    ! grep -qx 'open-webui' <<< "$lean_services" \
        || { echo 'fresh Core still selects WebUI' >&2; exit 1; }
    lean_images="$(WEBUI_SECRET=test-placeholder "${compose_base[@]}" \
        -f "$root/docker-compose.gateway-only.yml" config --images)"
    [[ "$lean_images" != *'open-webui'* ]] \
        || { echo 'fresh Core still pulls WebUI image' >&2; exit 1; }
    pixel_services="$(env WEBUI_SECRET=test-placeholder PIXEL_OPENWEBUI_KEY=test-placeholder \
        DASHBOARD_API_KEY=test-placeholder PIXEL_INGRESS_GID=1000 PIXEL_NATIVE_UID=1000 \
        PIXEL_INGRESS_RUNTIME_DIR=/tmp PIXEL_PREVIEW_RUNTIME_DIR=/tmp \
        PIXEL_NATIVE_WORKSPACE=/tmp PIXEL_NATIVE_CONFIG_PATH=/tmp/gateway.json \
        PIXEL_NATIVE_INGRESS_IMAGE=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
        "${compose_base[@]}" -f "$root/docker-compose.gateway-only.yml" \
        -f "$root/extensions/services/pixel-edge/compose.yaml.disabled" \
        -f "$root/installers/macos/pixel-native.compose.yaml.disabled" config --services)"
    grep -qx 'pixel-edge' <<< "$pixel_services" \
        || { echo 'lean native Pixel lost its Edge service' >&2; exit 1; }
    ! grep -qx 'open-webui' <<< "$pixel_services" \
        || { echo 'native Pixel overlay reselected WebUI' >&2; exit 1; }
else
    echo 'SKIP: Docker Compose unavailable for real service/image selection'
fi

# Execute the installer's actual success-summary producer under errexit and
# pipefail. Fresh Core disables every optional row; that must still reach the
# success card, while an unhealthy readiness result must stop the installer.
python3 - "$installer" "$scratch/readiness-producer.sh" <<'PY'
from pathlib import Path
import sys

lines = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
starts = [i for i, line in enumerate(lines[:-1])
          if line == "{" and "printf 'Dashboard|" in lines[i + 1]]
assert len(starts) == 1, "expected one installer readiness producer"
start = starts[0]
ends = [i for i in range(start + 1, len(lines))
        if lines[i].startswith('} | ods_readiness_summary ')]
assert len(ends) == 1, "expected one installer readiness pipeline"
Path(sys.argv[2]).write_text("\n".join(lines[start:ends[0] + 1]) + "\n", encoding="utf-8")
PY
cat > "$scratch/run-readiness.sh" <<'RUNNER'
#!/usr/bin/env bash
set -euo pipefail
ENABLE_OPEN_WEBUI=false
ENABLE_PERPLEXICA=false
ENABLE_VOICE=false
ENABLE_WORKFLOWS=false
ENABLE_OPENCODE=false
CLOUD_MODE=false
GATEWAY_ONLY=false
_macos_effective_service_enabled() { [[ "$2" == true ]]; }
OPENCODE_BIN=/nonexistent/opencode
OPENCODE_PORT=4096
ODS_LOG_FILE=/nonexistent/ods-install.log
_health_llama_host=127.0.0.1
_health_llama_port=8080
ods_readiness_summary() {
    cat > "$ROWS_FILE"
    return "${READINESS_RC:-0}"
}
source "$PRODUCER_FILE"
printf 'success card reached\n' > "$SENTINEL_FILE"
RUNNER
PRODUCER_FILE="$scratch/readiness-producer.sh" \
ROWS_FILE="$scratch/core-rows" SENTINEL_FILE="$scratch/core-success" \
    bash "$scratch/run-readiness.sh" \
    || { echo 'fresh Core exited after healthy readiness summary' >&2; exit 1; }
[[ -f "$scratch/core-success" ]] \
    || { echo 'fresh Core never reached its success card' >&2; exit 1; }
grep -q '^Dashboard|' "$scratch/core-rows" \
    || { echo 'Core summary omitted Dashboard' >&2; exit 1; }
! grep -Eq 'Open WebUI|OpenCode|Perplexica|Whisper|n8n' "$scratch/core-rows" \
    || { echo 'Core summary included an optional service' >&2; exit 1; }
if PRODUCER_FILE="$scratch/readiness-producer.sh" \
    ROWS_FILE="$scratch/failing-rows" SENTINEL_FILE="$scratch/failing-success" \
    READINESS_RC=7 bash "$scratch/run-readiness.sh"; then
    echo 'unhealthy readiness incorrectly reached success card' >&2
    exit 1
fi
[[ ! -e "$scratch/failing-success" ]] \
    || { echo 'unhealthy readiness wrote a success card' >&2; exit 1; }

echo 'PASS: Mac WebUI choice, retention, and lean Compose selection'
