#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"
pixel_load_env
pixel_require_command docker
docker compose version >/dev/null 2>&1 || pixel_die "Docker Compose v2 is required for reference services"

action=${1:-status}
confirm=${2:-}
runtime="$ROOT/.runtime"
settings="$runtime/searxng/settings.yml"
compose=(docker compose --project-directory "$ROOT" -f "$ROOT/deploy/compose.yaml")
export PIXEL_RUNTIME_DIR="$runtime"
if [[ ${PIXEL_ENABLE_REFERENCE_MODEL:-0} == 1 ]]; then export COMPOSE_PROFILES=local-model; fi

prepare() {
  install -d -m 700 "$runtime/searxng"
  if [[ ! -f "$settings" ]]; then
    pixel_require_command openssl
    secret=$(openssl rand -hex 32)
    sed "s/__PIXEL_SEARXNG_SECRET__/$secret/" "$ROOT/deploy/searxng/settings.yml.template" > "$settings"
    chmod 600 "$settings"
  fi
}

case "$action" in
  up)
    [[ ${PIXEL_DEPLOYMENT_PROFILE:-prepared} == reference ]] || pixel_die "Reference services require PIXEL_DEPLOYMENT_PROFILE=reference"
    [[ "$confirm" == --confirm ]] || pixel_die "Starting reference services requires: ./pixel services up --confirm"
    prepare
    if [[ ${PIXEL_ENABLE_REFERENCE_MODEL:-0} == 1 ]]; then
      [[ -f "$PIXEL_REFERENCE_MODEL_FILE" ]] || pixel_die "Reference model file not found: $PIXEL_REFERENCE_MODEL_FILE"
    fi
    "${compose[@]}" up -d
    ;;
  down)
    [[ "$confirm" == --confirm ]] || pixel_die "Stopping reference services requires: ./pixel services down --confirm"
    "${compose[@]}" down
    ;;
  status) "${compose[@]}" ps ;;
  logs) "${compose[@]}" logs --tail=200 ;;
  render) prepare; "${compose[@]}" config ;;
  --help|-h|help) echo "Usage: ./pixel services {up|down|status|logs|render} [--confirm]" ;;
  *) pixel_die "Unknown services action: $action" ;;
esac
