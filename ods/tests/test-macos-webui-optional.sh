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
    WEBUI_SECRET=test-placeholder "${compose_base[@]}" config --services \
        | grep -qx 'open-webui' \
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

echo 'PASS: Mac WebUI choice, retention, and lean Compose selection'
