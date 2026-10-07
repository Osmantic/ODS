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
mkdir -p "$fixture/data/user-extensions" "$fixture/extensions/services/pixel-agent" "$fixture/extensions/services/dashboard-api"
chmod 0775 "$fixture/data"
cp -R "$recipe" "$fixture/data/user-extensions/laya"
cp -R "$ods/extensions/services/pixel-agent/plugin" "$fixture/extensions/services/pixel-agent/plugin"
cp "$ods/extensions/services/dashboard-api/env_values.py" "$fixture/extensions/services/dashboard-api/"
printf 'LAYA_PORT=18017\n' > "$fixture/.env"
bash "$fixture/data/user-extensions/laya/setup.sh" "$fixture"
printf 'services:\n  laya:\n    image: ods-laya:qualification\n' > "$fixture/qualification.yaml"
compose=(docker compose --project-name ods-laya-qualification --project-directory "$fixture"
  -f "$fixture/data/user-extensions/laya/compose.yaml" -f "$fixture/qualification.yaml")
"${compose[@]}" config --quiet
docker build --tag ods-laya:qualification "$recipe"
docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --tmpfs /tmp:size=256m,mode=1777 \
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
if [[ "${LAYA_TEST_CUDA_IMAGE:-0}" == 1 ]]; then
  # Hosted runners have no NVIDIA GPU. Verify the actual CUDA dependency lock
  # on each architecture, then run real inference through the CPU fallback.
  docker build --target nvidia --tag ods-laya:qualification-cuda "$recipe"
  # Keep the existing CPU image tag and use the normal update/start command.
  # The recipe must rebuild for the selected target instead of silently
  # recreating the old CPU image with new environment/device settings.
  printf 'services:\n  laya:\n    image: ods-laya:qualification\n    build:\n      target: nvidia\n' > "$fixture/qualification.yaml"
  "${compose[@]}" up --detach --wait --wait-timeout 300
  "${compose[@]}" exec -T laya python -c 'import torch; assert torch.version.cuda, torch.__version__'
  export LAYA_TEST_EXPECT_DEVICE=cpu
  node "$recipe/tests/portal_adapter.integration.mjs"
fi
