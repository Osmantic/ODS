#!/usr/bin/env bash
# Run only on an isolated CI runner; creates its own ODS fixture and Compose project.
set -euo pipefail
: "${CI:?Use an isolated CI runner}"
recipe="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
ods="$(cd -- "$recipe/../../../.." && pwd -P)"
fixture="$(mktemp -d "$HOME/ods-laya-qa.XXXXXXXX")"
export ODS_UID ODS_GID LAYA_PORT
ODS_UID="$(id -u)"
ODS_GID="$(id -g)"
LAYA_PORT=18017
mkdir -p "$fixture/extensions/user" "$fixture/extensions/services/pixel-agent" "$fixture/extensions/services/dashboard-api"
cp -R "$recipe" "$fixture/extensions/user/laya"
cp -R "$ods/extensions/services/pixel-agent/plugin" "$fixture/extensions/services/pixel-agent/plugin"
cp "$ods/extensions/services/dashboard-api/env_values.py" "$fixture/extensions/services/dashboard-api/"
printf 'LAYA_PORT=18017\n' > "$fixture/.env"
bash "$fixture/extensions/user/laya/setup.sh" "$fixture"
printf 'services:\n  laya:\n    image: ods-laya:qualification\n' > "$fixture/qualification.yaml"
compose=(docker compose --project-name ods-laya-qualification --project-directory "$fixture"
  -f "$fixture/extensions/user/laya/compose.yaml" -f "$fixture/qualification.yaml")
"${compose[@]}" config --quiet
docker build --tag ods-laya:qualification "$recipe"
docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --mount "type=bind,source=$recipe,target=/recipe,readonly" \
  --entrypoint python ods-laya:qualification -m unittest discover -s /recipe/tests -p test_service.py -v
docker network create ods-network
cleanup() {
  status=$?
  trap - EXIT
  if (( status != 0 )); then "${compose[@]}" logs --no-color --tail 100; fi
  "${compose[@]}" down --volumes
  docker network rm ods-network
  exit "$status"
}
trap cleanup EXIT
"${compose[@]}" up --detach --no-build --wait --wait-timeout 900
export LAYA_TEST_KEY_FILE="$fixture/config/laya/api-key"
export LAYA_TEST_PORT="$LAYA_PORT" LAYA_TEST_INSTALL_ROOT="$fixture"
node "$recipe/tests/portal_adapter.integration.mjs"
node "$recipe/tests/lifecycle.integration.mjs"
"${compose[@]}" restart laya
"${compose[@]}" up --detach --no-build --wait --wait-timeout 300
node "$recipe/tests/portal_adapter.integration.mjs"
