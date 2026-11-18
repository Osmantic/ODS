#!/usr/bin/env bash
# The CLI on a vLLM install (LLM_BACKEND=vllm, docker-compose.vllm.yml): the
# vllm-server container serves the model and the in-stack llama-server is
# profiled out. A saved Compose file list from the other route is stale; "llm"
# and "llama-server" name vllm-server for logs and lifecycle commands (naming
# llama-server to Compose would enable its profile and start a second engine);
# status reports vllm-server from Docker's health (it has no host port) and no
# llama-server fault; ods mode keeps the route local and never points clients
# back at llama-server. Docker, curl and the resolver are stubs.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURE="$(mktemp -d)"
trap 'rm -rf "$FIXTURE"' EXIT
install_dir="$FIXTURE/install"
mkdir -p "$install_dir/lib" "$install_dir/extensions/services" "$install_dir/scripts" "$FIXTURE/bin"
cp "$ROOT_DIR/ods-cli" "$install_dir/ods-cli"
cp "$ROOT_DIR/scripts/mode-switch.sh" "$install_dir/scripts/mode-switch.sh"
for library in safe-env.sh service-registry.sh python-cmd.sh preflight-llm-route.sh; do
    cp "$ROOT_DIR/lib/$library" "$install_dir/lib/"
done
for compose in docker-compose.base.yml docker-compose.nvidia.yml docker-compose.vllm.yml; do
    printf 'services: {}\n' > "$install_dir/$compose"
done

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
pass() { printf 'PASS: %s\n' "$*"; }

for entry in 'llama-server core 8080 ods-llama-server [llm]' 'model-router core 9099 ods-model-router []' \
    'dashboard core 3001 ods-dashboard []'; do
    read -r service category port container aliases <<< "$entry"
    mkdir -p "$install_dir/extensions/services/$service"
    cat > "$install_dir/extensions/services/$service/manifest.yaml" <<MANIFEST
schema_version: ods.services.v1
service:
  id: $service
  name: $service
  aliases: $aliases
  container_name: $container
  category: $category
  port: $port
  health: /health
MANIFEST
done

VLLM_FLAGS='-f docker-compose.base.yml -f docker-compose.nvidia.yml -f docker-compose.vllm.yml'
# The resolver selects the vLLM overlay from the install's LLM_BACKEND.
cat > "$install_dir/scripts/resolve-compose-stack.sh" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
printf 'resolve\n' >> "$CLI_CALLS"
if grep -qx 'LLM_BACKEND=vllm' "$INSTALL_DIR/.env"; then
    printf '%s\n' '-f docker-compose.base.yml -f docker-compose.nvidia.yml -f docker-compose.vllm.yml'
else
    printf '%s\n' '-f docker-compose.base.yml -f docker-compose.nvidia.yml'
fi
STUB
chmod +x "$install_dir/scripts/resolve-compose-stack.sh"
cat > "$FIXTURE/bin/docker" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
printf 'docker %s\n' "$*" >> "$CLI_CALLS"
case "$*" in
    info*) printf '16\n' ;;
    'inspect --format '*' ods-vllm-server')
        [[ "$VLLM_STATE" == missing ]] && { printf 'Error: No such object: ods-vllm-server\n' >&2; exit 1; }
        printf '%s\n' "$VLLM_STATE" ;;
    compose*'ps --format {{.Service}}') printf 'vllm-server\nmodel-router\ndashboard\n' ;;
    compose*) ;;
    *) exit 64 ;;
esac
STUB
cat > "$FIXTURE/bin/curl" <<'STUB'
#!/usr/bin/env bash
printf 'curl %s\n' "$*" >> "$CLI_CALLS"
exit 0
STUB
chmod +x "$FIXTURE/bin/"*

export CLI_CALLS="$FIXTURE/calls" VLLM_STATE="running healthy"
write_env() {
    printf '%s\n' 'GPU_BACKEND=nvidia' 'ODS_MODE=local' 'LLM_BACKEND=vllm' \
        'LLM_MODEL=glm-5.3-flash-nvfp4' 'VLLM_SERVED_MODEL=GLM-5.3-Flash-NVFP4' \
        'ODS_MODEL_SWITCHBOARD=enabled' 'LLM_API_URL=http://litellm:4000' \
        'HERMES_DASHBOARD_SESSION_TOKEN=fixture-session' "$@" > "$install_dir/.env"
}
cli() {
    : > "$CLI_CALLS"
    INSTALL_DIR="$install_dir" ODS_HOME="$install_dir" HOME="$FIXTURE" PATH="$FIXTURE/bin:$PATH" \
        bash "$install_dir/ods-cli" "$@"
}

# 1. A Compose file list saved for llama-server is stale on the vLLM route,
# and the model service of logs is vllm-server.
write_env
printf '%s\n' '-f docker-compose.base.yml -f docker-compose.nvidia.yml' > "$install_dir/.compose-flags"
cli logs llm 5 > /dev/null
grep -qx 'resolve' "$CLI_CALLS" || fail 'a llama-server stack cache was reused on the vLLM route'
grep -qx "docker compose $VLLM_FLAGS logs -f --tail 5 vllm-server" "$CLI_CALLS" \
    || fail "ods logs llm did not follow vllm-server: $(cat "$CLI_CALLS")"
pass 'a llama-server stack cache is re-resolved and ods logs llm shows vllm-server'

printf '%s\n' "$VLLM_FLAGS -f extensions/services/hermes/compose.local.yaml" > "$install_dir/.compose-flags"
cli logs llama-server 5 > /dev/null
grep -qx 'resolve' "$CLI_CALLS" || fail 'a cache with llama-server waits was reused on the vLLM route'
grep -q ' logs -f --tail 5 vllm-server$' "$CLI_CALLS" || fail 'ods logs llama-server reached llama-server'
pass 'compose.local.yaml waits on llama-server make a vLLM cache stale'

printf '%s\n' "$VLLM_FLAGS" > "$install_dir/.compose-flags"
cli logs llm 5 > /dev/null
grep -q '^resolve$' "$CLI_CALLS" && fail 'a current vLLM cache was resolved again'
pass 'a current vLLM cache is reused'

write_env 'LLM_BACKEND=llama-server'
sed -i '/^LLM_BACKEND=vllm$/d' "$install_dir/.env"
printf '%s\n' "$VLLM_FLAGS" > "$install_dir/.compose-flags"
cli logs llm 5 > /dev/null
grep -qx 'resolve' "$CLI_CALLS" || fail 'a vLLM cache was reused on the llama-server route'
grep -q ' logs -f --tail 5 llama-server$' "$CLI_CALLS" || fail 'a llama-server install lost its llm alias'
pass 'a llama-server install re-resolves a vLLM cache and keeps llm on llama-server'

# 2. Lifecycle commands never name llama-server to Compose.
write_env
printf '%s\n' "$VLLM_FLAGS" > "$install_dir/.compose-flags"
cli restart llm > /dev/null
grep -q 'up -d --force-recreate --no-build --pull never vllm-server$' "$CLI_CALLS" \
    || fail "ods restart llm did not recreate vllm-server: $(cat "$CLI_CALLS")"
cli stop llm > /dev/null
grep -q ' stop vllm-server$' "$CLI_CALLS" || fail 'ods stop llm did not stop vllm-server'
cli start llama-server > /dev/null
grep -q ' up -d vllm-server$' "$CLI_CALLS" || fail 'ods start llama-server did not start vllm-server'
for command in restart stop start; do
    cli "$command" llm > /dev/null
    if grep -qE '^docker compose .* llama-server$' "$CLI_CALLS"; then
        fail "ods $command named llama-server to Compose"
    fi
done
pass 'restart, stop and start of llm or llama-server reach vllm-server only'

# 3. Status: vllm-server from Docker's health, no llama-server fault.
for state in 'running healthy:healthy' 'running starting:starting' 'running unhealthy:unhealthy' \
    'exited none:stopped' 'missing:stopped'; do
    VLLM_STATE="${state%%:*}"
    cli status --json > "$FIXTURE/status.json"
    jq -e --arg status "${state##*:}" '.services[] | select(.id == "vllm-server")
        | .status == $status and .containerStatus == "running" and .port == ""' "$FIXTURE/status.json" >/dev/null \
        || fail "vllm-server should be ${state##*:} for '$VLLM_STATE': $(cat "$FIXTURE/status.json")"
    jq -e 'all(.services[]; .id != "llama-server") and any(.services[]; .id == "model-router")' \
        "$FIXTURE/status.json" >/dev/null || fail 'status JSON reported llama-server or dropped model-router'
done
VLLM_STATE="running starting"
cli status > "$FIXTURE/status.txt" 2>&1
grep -q 'vLLM (LLM Inference): starting. A first start loads the model' "$FIXTURE/status.txt" \
    || fail "a cold start did not read as starting: $(cat "$FIXTURE/status.txt")"
grep -q 'llama-server' "$FIXTURE/status.txt" && fail "status mentioned llama-server: $(cat "$FIXTURE/status.txt")"
grep -q '127.0.0.1:8080/health' "$CLI_CALLS" && fail 'status probed the profiled-out llama-server port'
VLLM_STATE="running healthy"
cli status > "$FIXTURE/status.txt" 2>&1
grep -q 'vLLM (LLM Inference): healthy' "$FIXTURE/status.txt" || fail 'a ready vLLM engine was not healthy'
pass 'status reports vllm-server from Docker health, starting during a cold start, and no llama-server'

# 4. ods mode and mode-switch.sh keep the vLLM route local.
for mode in cloud hybrid; do
    write_env
    cp "$install_dir/.env" "$FIXTURE/before.env"
    if cli mode "$mode" > "$FIXTURE/mode.out" 2>&1; then fail "ods mode $mode was accepted on vLLM"; fi
    grep -q 'vLLM engine, which works only in local mode. Nothing was changed.' "$FIXTURE/mode.out" \
        || fail "ods mode $mode refusal: $(cat "$FIXTURE/mode.out")"
    grep -q 'discord.gg' "$FIXTURE/mode.out" || fail 'the refusal names no help'
    cmp -s "$FIXTURE/before.env" "$install_dir/.env" || fail "ods mode $mode changed .env"
    if (cd "$install_dir/scripts" && bash ./mode-switch.sh "$mode") > "$FIXTURE/mode.out" 2>&1; then
        fail "mode-switch.sh $mode was accepted on vLLM"
    fi
    grep -q 'works only in local mode' "$FIXTURE/mode.out" || fail "mode-switch.sh $mode refusal"
    cmp -s "$FIXTURE/before.env" "$install_dir/.env" || fail "mode-switch.sh $mode changed .env"
done
write_env 'LLM_API_URL=http://llama-server:8080' 'ODS_MODE=hybrid'
cli mode local > /dev/null
grep -qx 'LLM_API_URL=http://litellm:4000' "$install_dir/.env" || fail 'ods mode local pointed back at llama-server'
grep -qx 'HERMES_LLM_BASE_URL=http://model-router:9099/v1' "$install_dir/.env" || fail 'Hermes left model-router'
write_env 'LLM_API_URL=http://llama-server:8080'
(cd "$install_dir/scripts" && bash ./mode-switch.sh local) > /dev/null
grep -qx 'LLM_API_URL=http://litellm:4000' "$install_dir/.env" || fail 'mode-switch.sh local pointed back at llama-server'
cli mode > "$FIXTURE/mode.out" 2>&1
grep -q 'LLM:  Local vLLM engine (vllm-server)' "$FIXTURE/mode.out" || fail 'ods mode did not name the vLLM engine'
pass 'ods mode refuses cloud and hybrid on vLLM and local keeps clients off llama-server'

# 5. ods doctor's summary names the vLLM engine as ODS's own local model server.
awk '/python3 - "\$report_file" <<'"'"'PY'"'"'/ {take=1; next} take && /^PY$/ {exit} take {print}' "$ROOT_DIR/ods-cli" > "$FIXTURE/doctor-view.py"
[[ -s "$FIXTURE/doctor-view.py" ]] || fail 'the doctor report renderer was not found in ods-cli'
printf '%s\n' '{"runtime": {"llm_backend": {"status": "ok", "provider": "vLLM", "url": "http://vllm-server:8000 (ODS network only)"}}}' > "$FIXTURE/doctor.json"
# The renderer exits 1 when this partial report lacks the Docker checks; only its LLM line matters here.
view_status=0
PYTHONIOENCODING=utf-8 python3 "$FIXTURE/doctor-view.py" "$FIXTURE/doctor.json" > "$FIXTURE/doctor.out" 2>&1 || view_status=$?
[[ "$view_status" -le 1 ]] || fail "the doctor report renderer crashed ($view_status): $(cat "$FIXTURE/doctor.out")"
grep -q 'LLM Backend: vLLM (local) — responding' "$FIXTURE/doctor.out" \
    || fail "ods doctor must call the vLLM engine local: $(cat "$FIXTURE/doctor.out")"
pass 'ods doctor names the vLLM engine as a local model server'
