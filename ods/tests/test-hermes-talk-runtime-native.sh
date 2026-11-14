#!/usr/bin/env bash
# Isolated integration tests against the installed, digest-pinned Hermes image.
# No host data, network access, agent execution or model calls.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
runtime="$root/extensions/services/hermes/talk-runtime"
fixtures="$root/tests/fixtures/hermes-talk-runtime"
image='nousresearch/hermes-agent:v2026.9.24@sha256:fca358f12efd65bfaaca05884166f15c0e2788375ca30d77061ac1ebc96452b7'
docker image inspect "$image" >/dev/null
for test in adapter_contract cold_resume transport_reattach bridge_transport; do
  docker run --rm --network none --read-only \
    --label ods.fleet.test=hermes-talk-native \
    --tmpfs /tmp:rw,nosuid,nodev,size=128m --memory 768m --cpus 2 \
    --workdir /opt/hermes --env PYTHONDONTWRITEBYTECODE=1 \
    --env PYTHONPATH=/opt/ods-hermes-runtime \
    --mount "type=bind,source=$runtime,target=/opt/ods-hermes-runtime,readonly" \
    --mount "type=bind,source=$fixtures,target=/ods-fixtures,readonly" \
    --mount "type=bind,source=$root/extensions/services/dashboard-api,target=/opt/ods-dashboard-api,readonly" \
    --entrypoint /opt/hermes/.venv/bin/python "$image" "/ods-fixtures/$test.py"
done

# The production bootstrap remaps the UID and passes through s6, which
# rehydrates its environment. Prove sitecustomize survives that full path.
# All writes are confined to this disposable container, with no network or
# host data. The seed below is deliberately public test data.
docker run --rm --network none --memory 768m --cpus 2 \
  --label ods.fleet.test=hermes-talk-native \
  --tmpfs /tmp:rw,nosuid,nodev,size=128m \
  --tmpfs /opt/data:rw,nosuid,nodev,size=128m \
  --workdir /opt/hermes --env PYTHONDONTWRITEBYTECODE=1 \
  --env PYTHONPATH=/opt/ods-hermes-runtime \
  --env HERMES_UID=10000 --env HERMES_GID=10000 \
  --env HERMES_HOME=/opt/data --env HERMES_DASHBOARD=0 \
  --env HERMES_DASHBOARD_SESSION_TOKEN=ods-isolated-fixture-seed \
  --mount "type=bind,source=$runtime,target=/opt/ods-hermes-runtime,readonly" \
  --mount "type=bind,source=$fixtures,target=/ods-fixtures,readonly" \
  --mount "type=bind,source=$root/extensions/services/dashboard-api/hermes_auth.py,target=/opt/hermes/ods-auth-bootstrap.py,readonly" \
  --entrypoint /opt/hermes/.venv/bin/python "$image" \
  /opt/hermes/ods-auth-bootstrap.py /opt/hermes/.venv/bin/python /ods-fixtures/startup_probe.py
