#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/ods-capability-stdout.XXXXXX")"
trap 'rm -rf "$FIXTURE"' EXIT

mkdir -p "$FIXTURE/scripts" "$FIXTURE/lib"
cp "$ROOT_DIR/scripts/build-capability-profile.sh" "$FIXTURE/scripts/"
cp "$ROOT_DIR/lib/safe-env.sh" "$FIXTURE/lib/"
cat > "$FIXTURE/lib/service-registry.sh" <<'SH'
declare -A SERVICE_PORTS=([llama-server]=8080)
declare -A SERVICE_HEALTH=([llama-server]=/health)
sr_load() { :; }
sr_resolve_ports() { :; }
SH

cat > "$FIXTURE/scripts/detect-hardware.sh" <<'SH'
#!/usr/bin/env bash
printf '%s\n' '{"os":"linux","cpu":"Fixture CPU","ram_gb":32,"tier":"T2","gpu":{"type":"nvidia","name":"Fixture GPU","memory_type":"discrete","vram_mb":16384,"count":1}}'
SH
cat > "$FIXTURE/scripts/classify-hardware.sh" <<'SH'
#!/usr/bin/env bash
cat <<'ENV'
HW_CLASS_ID="fixture"
HW_CLASS_LABEL="Fixture Host"
HW_REC_BACKEND="nvidia"
HW_REC_TIER="T2"
HW_REC_COMPOSE_OVERLAYS="docker-compose.base.yml,docker-compose.nvidia.yml"
ENV
SH
chmod +x "$FIXTURE/scripts/detect-hardware.sh" "$FIXTURE/scripts/classify-hardware.sh"

json_out="$(bash "$FIXTURE/scripts/build-capability-profile.sh" --stdout)"
[[ ! -e "$FIXTURE/.capabilities.json" ]]
JSON_REPORT="$json_out" python3 - <<'PY'
import json
import os

report = json.loads(os.environ["JSON_REPORT"])
assert report["version"] == "1"
assert report["hardware_class"] == {"id": "fixture", "label": "Fixture Host"}
assert report["runtime"]["llm_backend"] == "nvidia"
assert report["compose"]["overlays"] == [
    "docker-compose.base.yml",
    "docker-compose.nvidia.yml",
]
PY

set +e
error_out="$(bash "$FIXTURE/scripts/build-capability-profile.sh" --stdout --env 2>&1)"
status=$?
set -e
[[ $status -eq 1 ]]
[[ "$error_out" == *"mutually exclusive"* ]]

echo "Capability profile stdout tests passed"
