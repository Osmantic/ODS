#!/usr/bin/env bash
# Disposable Ubuntu Docker Engine journey at one pinned product SHA.
set -euo pipefail

product="${ODS_ACCEPTANCE_PRODUCT_ROOT:?product checkout is required}"
harness="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$RUNNER_TEMP/ods-hermes-acceptance"
export INSTALL_DIR="$root/install" LOG_FILE="$root/install.log"
key_file="$root/mock.key"
mock_log="$root/mock.log"
mock_port=18080
model=ods-acceptance-mock
mock_pid=""

cleanup() {
    if [[ -n "$mock_pid" ]]; then
        kill "$mock_pid" 2>/dev/null || true
        wait "$mock_pid" 2>/dev/null || true
    fi
}
trap cleanup EXIT

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    if [[ -f "$root/hermes-enable.json" ]]; then
        printf 'Hermes diagnostic (private values redacted):\n' >&2
        for source in "$INSTALL_DIR/data/extension-progress/hermes.json"; do
            if [[ -f "$source" ]]; then
                python3 - "$source" "$key_file" <<'PY' >&2 || true
import json
import sys
from pathlib import Path
key = Path(sys.argv[2]).read_text(encoding="ascii").strip()
data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(json.dumps({name: str(data.get(name, "")).replace(key, "[redacted]")
                  for name in ("status", "message", "error")}, sort_keys=True))
PY
            fi
        done
        journalctl -u ods-host-agent.service --since '4 minutes ago' --no-pager 2>/dev/null \
            | grep -Ei 'hermes|compose.*fail|extension.*start' | tail -n 15 \
            | python3 -c 'import sys; from pathlib import Path; key=Path(sys.argv[1]).read_text().strip(); print(sys.stdin.read().replace(key,"[redacted]"))' "$key_file" >&2 || true
        docker logs ods-dashboard-api --tail 100 2>&1 \
            | grep -Ei 'hermes|Host agent unreachable' | tail -n 15 \
            | python3 -c 'import sys; from pathlib import Path; key=Path(sys.argv[1]).read_text().strip(); print(sys.stdin.read().replace(key,"[redacted]"))' "$key_file" >&2 || true
        if [[ "$*" == *'Hermes Library add-back reported failure'* ]]; then
            image='nousresearch/hermes-agent:v2026.9.24@sha256:fca358f12efd65bfaaca05884166f15c0e2788375ca30d77061ac1ebc96452b7'
            if timeout 180s docker pull "$image" >"$root/hermes-pull-diagnostic.log" 2>&1; then
                printf 'Diagnostic direct Hermes image pull succeeded.\n' >&2
            else
                printf 'Diagnostic direct Hermes image pull failed:\n' >&2
                tail -n 18 "$root/hermes-pull-diagnostic.log" \
                    | python3 -c 'import sys; from pathlib import Path; key=Path(sys.argv[1]).read_text().strip(); print(sys.stdin.read().replace(key,"[redacted]"))' "$key_file" >&2 || true
            fi
            diagnostic_resolved="$(cd "$INSTALL_DIR" && \
                ODS_EXTERNAL_LLM_SELECTED=true ODS_GATEWAY_ONLY=true \
                ENABLE_OPEN_WEBUI=false ODS_MODE=local \
                bash scripts/resolve-compose-stack.sh --script-dir "$INSTALL_DIR" \
                    --tier 1 --gpu-backend cpu --ods-mode local)" || diagnostic_resolved=""
            if [[ -n "$diagnostic_resolved" ]]; then
                read -r -a diagnostic_flags <<<"$diagnostic_resolved"
                if (cd "$INSTALL_DIR" && timeout 180s docker compose "${diagnostic_flags[@]}" up -d hermes) \
                    >"$root/hermes-compose-diagnostic.log" 2>&1; then
                    printf 'Diagnostic direct Compose start succeeded after failed agent start.\n' >&2
                else
                    printf 'Diagnostic direct Compose start failed:\n' >&2
                    tail -n 22 "$root/hermes-compose-diagnostic.log" \
                        | python3 -c 'import sys; from pathlib import Path; key=Path(sys.argv[1]).read_text().strip(); print(sys.stdin.read().replace(key,"[redacted]"))' "$key_file" >&2 || true
                fi
            fi
        fi
    fi
    docker ps --format '{{.Names}} {{.Status}}' >&2 || true
    exit 1
}

compose_services() (
    cd "$INSTALL_DIR"
    local flags resolved
    resolved="$(ODS_EXTERNAL_LLM_SELECTED=true ODS_GATEWAY_ONLY=true \
        ENABLE_OPEN_WEBUI=false ODS_MODE=local \
        bash scripts/resolve-compose-stack.sh --script-dir "$INSTALL_DIR" \
            --tier 1 --gpu-backend cpu --ods-mode local)" || return 1
    read -r -a flags <<<"$resolved"
    docker compose "${flags[@]}" config --services
)

wait_hermes() {
    local state=""
    for _ in {1..100}; do
        state="$(docker inspect --format '{{.State.Health.Status}}' ods-hermes 2>/dev/null || true)"
        [[ "$state" == healthy ]] && return 0
        [[ "$state" == unhealthy ]] && return 1
        sleep 5
    done
    return 1
}

[[ "$(git -C "$product" rev-parse HEAD)" == 3f413199bcf1caf59430273870079a5b98bb41f4 ]] \
    || fail 'wrong product checkout'
command -v docker >/dev/null || fail 'Docker CLI missing'
docker info >/dev/null || fail 'isolated Docker Engine unavailable'
[[ ! -e "$INSTALL_DIR" ]] || fail 'install directory already exists'
mkdir -p "$root"
umask 022
python3 - "$key_file" <<'PY'
import secrets
import sys
with open(sys.argv[1], "w", encoding="ascii") as stream:
    stream.write("mock-" + secrets.token_hex(24))
PY
chmod 600 "$key_file"
python3 "$harness/mock-openai-upstream.py" --key-file "$key_file" \
    --port "$mock_port" >"$mock_log" 2>&1 &
mock_pid=$!
for _ in {1..30}; do
    curl -fsS --max-time 2 "http://127.0.0.1:$mock_port/healthz" >/dev/null 2>&1 && break
    sleep 1
done
curl -fsS --max-time 2 "http://127.0.0.1:$mock_port/healthz" >/dev/null \
    || fail 'mock upstream did not start'

docker image ls --format '{{.Repository}}:{{.Tag}}' | LC_ALL=C sort -u >"$root/images-before.txt"
if ! (cd "$product" && timeout 1500s bash install-core.sh \
    --non-interactive --skip-docker --no-pixel --gateway-only \
    --external-llm-url "http://127.0.0.1:$mock_port" \
    --external-llm-provider openai-compatible \
    --external-llm-model "$model" --external-llm-key-file "$key_file") \
    >>"$LOG_FILE" 2>&1; then
    fail 'fresh gateway installer did not complete'
fi
services="$(compose_services)" || fail 'fresh gateway Compose selection failed'
for required in dashboard dashboard-api litellm; do
    grep -qx "$required" <<<"$services" || fail "gateway omitted $required"
done
for forbidden in open-webui llama-server model-router hermes searxng; do
    if grep -qx "$forbidden" <<<"$services"; then fail "gateway selected $forbidden"; fi
done
printf 'PASS: fresh gateway has no Hermes, SearXNG, or managed llama\n'

# Prove the managed-local health gate without allocating a model or GPU.
cat >"$root/unhealthy-llama.yml" <<'YAML'
services:
  llama-server:
    image: busybox:1.36
    command: ["sleep", "120"]
    healthcheck:
      test: ["CMD", "false"]
      interval: 2s
      timeout: 1s
      retries: 2
  hermes:
    image: busybox:1.36
    entrypoint: ["sleep"]
    command: ["120"]
YAML
local_definition="$INSTALL_DIR/extensions/services/hermes/compose.yaml.disabled"
local_overlay="$INSTALL_DIR/extensions/services/hermes/compose.local.yaml"
[[ -f "$local_definition" && -f "$local_overlay" ]] \
    || fail 'installed managed-local Hermes definition is missing'
local_stack=(docker compose -p ods-hermes-unhealthy --project-directory "$INSTALL_DIR" \
    --env-file "$INSTALL_DIR/.env" -f "$local_definition" -f "$local_overlay" \
    -f "$root/unhealthy-llama.yml")
"${local_stack[@]}" config --format json >"$root/local-compose.json" \
    || fail 'managed-local Hermes Compose did not render'
python3 - "$root/local-compose.json" <<'PY' || fail 'managed-local health dependency is absent'
import json
import sys
config = json.load(open(sys.argv[1], encoding="utf-8"))
assert config["services"]["hermes"]["depends_on"]["llama-server"]["condition"] == "service_healthy"
assert config["services"]["hermes"]["image"] == "busybox:1.36"
PY
if timeout 120s "${local_stack[@]}" up -d --wait hermes >"$root/unhealthy-start.log" 2>&1; then
    fail 'managed-local Hermes started against an unhealthy llama'
fi
grep -Eiq 'unhealthy|dependency failed' "$root/unhealthy-start.log" \
    || fail 'managed-local start failed for a reason other than llama health'
stub_id="$("${local_stack[@]}" ps -q llama-server)"
[[ -n "$stub_id" && "$(docker inspect --format '{{.State.Health.Status}}' "$stub_id")" == unhealthy ]] \
    || fail 'managed-local llama stand-in was not unhealthy'
"${local_stack[@]}" down --volumes --remove-orphans >/dev/null \
    || fail 'could not clean disposable unhealthy-llama project'
printf 'PASS: managed-local Hermes waits for a healthy llama-server\n'

# The public Library endpoint must add SearXNG, then Hermes, without llama.
curl -fsS --max-time 30 http://127.0.0.1:3001/api/extensions/hermes/install-plan \
    >"$root/hermes-plan.json" || fail 'Hermes install plan unavailable'
python3 - "$root/hermes-plan.json" <<'PY' || fail 'Hermes plan includes managed llama'
import json
import sys
plan = json.load(open(sys.argv[1], encoding="utf-8"))
steps = [(step["extensionId"], step["action"]) for step in plan["steps"]]
assert plan["blocked"] is False, plan
assert steps == [("searxng", "enable"), ("hermes", "enable")], steps
PY
code="$(curl -sS --max-time 1500 -o "$root/hermes-enable.json" -w '%{http_code}' \
    -X POST 'http://127.0.0.1:3001/api/extensions/hermes/enable?auto_enable_deps=true' || true)"
[[ "$code" == 200 ]] || fail "Hermes Library add-back returned HTTP $code"
python3 - "$root/hermes-enable.json" <<'PY' || fail 'Hermes Library add-back reported failure'
import json
import sys
result = json.load(open(sys.argv[1], encoding="utf-8"))
assert result.get("failed_services") == [], result
assert "hermes" in result.get("enabled_services", []), result
assert "searxng" in result.get("enabled_services", []), result
PY
wait_hermes || fail 'Hermes did not become healthy after Library add-back'
[[ -f "$INSTALL_DIR/data/persona/SOUL.md" && -s "$INSTALL_DIR/data/persona/SOUL.md" ]] \
    || fail 'Hermes persona was not materialized as a nonempty file'
[[ "$(stat -c %a "$INSTALL_DIR/data/persona/SOUL.md")" == 644 ]] \
    || fail 'Hermes persona file mode is not readable by its container'
for _ in {1..36}; do
    curl -fsS --max-time 5 http://127.0.0.1:8888/healthz >/dev/null 2>&1 && break
    sleep 5
done
curl -fsS --max-time 20 http://127.0.0.1:8888/healthz >/dev/null \
    || fail 'Hermes search dependency did not become healthy'
services="$(compose_services)" || fail 'enabled gateway Compose selection failed'
grep -qx hermes <<<"$services" || fail 'Hermes missing from enabled stack'
grep -qx searxng <<<"$services" || fail 'SearXNG missing from enabled stack'
if grep -qx llama-server <<<"$services"; then fail 'managed llama entered enabled stack'; fi
if docker ps --filter 'label=com.docker.compose.service=llama-server' \
    --format '{{.Names}}' | grep -q .; then
    fail 'managed llama container started'
fi
docker image ls --format '{{.Repository}}:{{.Tag}}' | LC_ALL=C sort -u >"$root/images-after.txt"
if comm -13 "$root/images-before.txt" "$root/images-after.txt" | grep -Ei 'llama.cpp|llama-server'; then
    fail 'Hermes add-back pulled a managed llama image'
fi
printf 'PASS: Hermes and SearXNG added without managed llama\n'

python3 - "$INSTALL_DIR" <<'PY' || fail 'Hermes private model route is wrong'
import os
import stat
import sys
from pathlib import Path
import yaml
root = Path(sys.argv[1])
values = dict(line.rstrip("\n").split("=", 1) for line in (root / ".env").read_text().splitlines()
              if "=" in line and not line.startswith("#"))
key = values["HERMES_LLM_API_KEY"].strip().strip('"').strip("'")
live = root / "data/hermes/config.yaml"
template = root / "extensions/services/hermes/cli-config.yaml.template"
model = yaml.safe_load(live.read_text(encoding="utf-8"))["model"]
print("Hermes route diagnostic:", {
    "model_default": model.get("default"),
    "base_url": model.get("base_url"),
    "api_key_present": bool(model.get("api_key")),
    "api_key_matches_env": model.get("api_key") == key,
    "mode": oct(stat.S_IMODE(live.stat().st_mode)),
}, flush=True)
assert model["default"] == "ods-acceptance-mock"
assert model["base_url"] == "http://litellm:4000/v1"
assert model["api_key"] == key and key
# Host pre-start writes 0600; Hermes may add owner-group read after startup.
assert stat.S_IMODE(live.stat().st_mode) in (0o600, 0o640)
assert key not in template.read_text(encoding="utf-8")
print("PASS: Hermes selected external model in private live config")
PY

# Exercise the installed dashboard-to-Hermes bridge and observe the mock model.
hits_before="$(grep -c 'accept=chat bytes=' "$mock_log" || true)"
if ! timeout 360s docker exec -i ods-dashboard-api python - <<'PY'
import asyncio
from hermes_bridge import stream_prompt

async def check():
    answer = ""
    async for event in stream_prompt("ods-hermes-acceptance", "Reply exactly OK."):
        if event.get("type") == "error":
            raise RuntimeError("Hermes stream returned an error")
        if event.get("type") == "complete":
            answer = str(event.get("text") or "")
    assert "OK" in answer, "Hermes returned no model-backed answer"

asyncio.run(check())
print("PASS: Hermes answered through the installed dashboard bridge")
PY
then
    fail 'Hermes did not answer through the installed dashboard bridge'
fi
hits_after="$(grep -c 'accept=chat bytes=' "$mock_log" || true)"
(( hits_after > hits_before )) || fail 'Hermes reply did not reach selected external model'

printf retained-hermes-data >"$INSTALL_DIR/data/hermes/ods-acceptance-sentinel" \
    || fail 'could not write Hermes data sentinel'
curl -fsS --max-time 180 -X POST http://127.0.0.1:3001/api/extensions/hermes/disable \
    >"$root/hermes-disable.json" || fail 'Hermes Disable failed'
curl -fsS --max-time 1500 -X POST \
    'http://127.0.0.1:3001/api/extensions/hermes/enable?auto_enable_deps=true' \
    >"$root/hermes-reenable.json" || fail 'Hermes re-enable failed'
wait_hermes || fail 'Hermes did not recover after re-enable'
grep -qx retained-hermes-data "$INSTALL_DIR/data/hermes/ods-acceptance-sentinel" \
    || fail 'Hermes data changed across Disable and re-enable'
printf 'PASS: Hermes data retained across Disable and re-enable\n'
