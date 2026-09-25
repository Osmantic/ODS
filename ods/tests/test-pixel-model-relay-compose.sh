#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    echo '[SKIP] Docker Compose is unavailable for Pixel relay routing matrix'
    exit 0
fi

for mode in managed cloud external; do
    flags=(
        -f docker-compose.base.yml
        -f docker-compose.cpu.yml
        -f extensions/services/litellm/compose.yaml
        -f extensions/services/pixel-model-relay/compose.yaml.disabled
    )
    ods_mode=local external_url=
    case "$mode" in
        managed) ;;
        cloud) ods_mode=cloud; flags+=(-f docker-compose.cloud.yml) ;;
        external)
            external_url=http://host.docker.internal:8080
            flags+=(-f docker-compose.external-llm.yml)
            ;;
    esac
    rendered="$(PIXEL_MODEL_RELAY_KEY=test-relay-key LITELLM_KEY=test-litellm-key \
        ODS_MODE="$ods_mode" EXTERNAL_LLM_URL="$external_url" \
        docker compose --env-file .env.example "${flags[@]}" config --format json)"
    printf '%s\n' "$rendered" | python3 -c '
import json
import sys

mode = sys.argv[1]
services = json.load(sys.stdin)["services"]
assert "pixel-model-relay" in services
assert "litellm" in services
assert ("model-router" in services) == (mode == "managed")
relay_env = services["pixel-model-relay"]["environment"]
assert relay_env["ODS_MODE"] == ("cloud" if mode == "cloud" else "local")
assert bool(relay_env["EXTERNAL_LLM_URL"]) == (mode == "external")
assert relay_env["LITELLM_KEY"] == "test-litellm-key"
' "$mode"
done
echo 'Pixel relay Compose routes pass managed, cloud, and external modes'
