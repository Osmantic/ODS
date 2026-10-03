#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
installer="$root/installers/macos/install-macos.sh"
scratch="$(mktemp -d)"
trap 'rm -rf -- "$scratch"' EXIT
python3 - "$installer" <<'PY'
from pathlib import Path
import sys
s=Path(sys.argv[1]).read_text()
assembly=s.index('# ── Assemble Docker Compose flags')
call=s.index('if ! _macos_include_retained_pixel_compose;',assembly)
validation=s.index('# ── Validate compose files exist',assembly)
launch=s.index('docker compose "${COMPOSE_FLAGS[@]}" "${_macos_compose_up_args[@]}"',validation)
assert assembly < call < validation < launch
assert s.index('"$LIB_DIR/pixel-native-retain.py" "${_pixel_copied_retain_args[@]}"') < assembly
PY
eval "$(sed -n '/^_macos_include_retained_pixel_compose() {/,/^}/p' "$installer")"
INSTALL_DIR="$scratch/install"
LIB_DIR="$root/installers/macos/lib"
mkdir -p "$INSTALL_DIR"
fixture() {
    python3 - "$root" "$INSTALL_DIR" "${1:-normal}" <<'PY'
import hashlib,json,pathlib,shutil,sys
source,root=map(pathlib.Path,sys.argv[1:3]);legacy=sys.argv[3]=='legacy'
prep=root/'data/pixel-native/preparation';prep.mkdir(parents=True,exist_ok=True)
for rel in ('extensions/services/pixel-model-relay/compose.yaml.disabled','extensions/services/pixel-edge/compose.yaml.disabled','installers/macos/pixel-native.compose.yaml.disabled'):
    target=root/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source/rel,target)
prepared={'status':'prepared','phase':'awaiting-protected-activation','home':str(root/'data/pixel-native/home'),'runtimeDigest':'a'*64,'serviceDigest':'b'*64}
active={'status':'ready','phase':'services-ready','runtimeDigest':'a'*64,'serviceDigest':'b'*64}
if legacy:
    prepared.update(kind='legacy-native',phase='awaiting-joint-activation',installDir=str(root),currentDigest='c'*64)
    volumes={name:{'external':True,'name':'retained-'+name} for name in ('pixel-native-runtime','pixel-native-previews','pixel-native-preview-runtime','pixel-transition-state')}
    storage={'volumes':volumes};digest=hashlib.sha256(json.dumps(storage,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    prepared['storageDigest']=active['storageDigest']=digest
    (prep/'storage.compose.json').write_text(json.dumps(storage))
(prep/'preparation.json').write_text(json.dumps(prepared));(prep/'activation.json').write_text(json.dumps(active))
PY
}
ENABLE_PIXEL=true _PIXEL_RETAINED=false
COMPOSE_FLAGS=(-f docker-compose.base.yml)
_macos_include_retained_pixel_compose
[[ "${COMPOSE_FLAGS[*]}" == '-f docker-compose.base.yml' ]]
ENABLE_PIXEL=false _PIXEL_RETAINED=true
_macos_include_retained_pixel_compose
[[ "${COMPOSE_FLAGS[*]}" == '-f docker-compose.base.yml' ]]
ENABLE_PIXEL=true
fixture
_macos_include_retained_pixel_compose
expected='-f docker-compose.base.yml -f extensions/services/pixel-model-relay/compose.yaml.disabled -f extensions/services/pixel-edge/compose.yaml.disabled -f installers/macos/pixel-native.compose.yaml.disabled'
[[ "${COMPOSE_FLAGS[*]}" == "$expected" ]]
COMPOSE_FLAGS+=(-f extensions/services/pixel-edge/compose.yaml.disabled)
_macos_include_retained_pixel_compose
[[ "${COMPOSE_FLAGS[*]}" == "$expected" ]]
printf '{}' > "$INSTALL_DIR/data/pixel-native/preparation/activation.json"
before="${COMPOSE_FLAGS[*]}"
if _macos_include_retained_pixel_compose 2>/dev/null; then
    echo 'malformed retained receipt was accepted' >&2; exit 1
fi
[[ "${COMPOSE_FLAGS[*]}" == "$before" ]]
fixture legacy
COMPOSE_FLAGS=(-f docker-compose.base.yml -f extensions/services/pixel-vm-link/compose.yaml)
_macos_include_retained_pixel_compose
[[ "${COMPOSE_FLAGS[*]}" == "$expected -f data/pixel-native/preparation/storage.compose.json" ]]
fixture
COMPOSE_FLAGS=(-f docker-compose.base.yml -f installers/macos/docker-compose.macos.yml -f docker-compose.gateway-only.yml)
_macos_include_retained_pixel_compose
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    config="$scratch/compose.json"
    (
        cd "$root"
        env WEBUI_SECRET=fixture-key PIXEL_OPENWEBUI_KEY=fixture-pixel-key \
            DASHBOARD_API_KEY=fixture-dashboard-key PIXEL_INGRESS_GID=1000 PIXEL_NATIVE_UID=1000 \
            PIXEL_INGRESS_RUNTIME_DIR=/tmp PIXEL_PREVIEW_RUNTIME_DIR=/tmp \
            PIXEL_NATIVE_WORKSPACE=/tmp PIXEL_NATIVE_CONFIG_PATH=/tmp/gateway.json \
            PIXEL_NATIVE_INGRESS_IMAGE=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
            PIXEL_MODEL_RELAY_KEY=fixture-relay-key \
            docker compose "${COMPOSE_FLAGS[@]}" config --format json > "$config"
    )
    python3 - "$config" <<'PY'
import json,sys
services=json.load(open(sys.argv[1]))['services']
assert all(name in services for name in ('pixel-edge','pixel-model-relay','pixel-native-ingress','dashboard-api'))
assert 'open-webui' not in services
api=services['dashboard-api']['environment']
assert api['PIXEL_OPENWEBUI_KEY']=='fixture-pixel-key'
assert api['PIXEL_EDGE_URL']=='http://pixel-edge:9595'
PY
    # Exercise the actual installer build selector against that rendered stack.
    _macos_enabled_services="$(python3 -c 'import json,sys; print("\n".join(json.load(open(sys.argv[1]))["services"]))' "$config")"
    log() { :; }
    eval "$(sed -n '/^    _macos_candidate_build_services=/p' "$installer")"
    eval "$(sed -n '/^    _macos_build_services=()/,/^    done/p' "$installer")"
    for service in pixel-edge pixel-model-relay pixel-workspace-preview; do
        [[ " ${_macos_build_services[*]} " == *" $service "* ]] || {
            echo "retained local image omitted from rebuild: $service" >&2; exit 1;
        }
    done
else
    echo 'SKIP: real Compose selection unavailable'
fi
echo 'PASS: retained Pixel Compose selected before base launch; optional WebUI remains disabled'
