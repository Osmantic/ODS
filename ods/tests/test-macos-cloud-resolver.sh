#!/usr/bin/env bash
# Behavioral coverage for resolver-driven macOS cloud stack reconstruction.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESOLVER="$ROOT_DIR/scripts/resolve-compose-stack.sh"
TMP_DIR="$(mktemp -d)"
FIXTURE="$TMP_DIR/ods"
AUTH_REL="data/generated/docker-compose.macos-cloud-auth.yml"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

normalize_paths() {
    tr '\134' '/' | sed 's#//*#/#g'
}

mkdir -p "$FIXTURE/data/generated" "$FIXTURE/lib"
cp "$ROOT_DIR/docker-compose.base.yml" "$FIXTURE/docker-compose.base.yml"
cp "$ROOT_DIR/docker-compose.cloud.yml" "$FIXTURE/docker-compose.cloud.yml"
cp "$ROOT_DIR/docker-compose.nvidia.yml" "$FIXTURE/docker-compose.nvidia.yml"
if [[ -f "$ROOT_DIR/docker-compose.multigpu-nvidia.yml" ]]; then
    cp "$ROOT_DIR/docker-compose.multigpu-nvidia.yml" "$FIXTURE/docker-compose.multigpu-nvidia.yml"
fi
cp "$ROOT_DIR/lib/python-cmd.sh" "$FIXTURE/lib/python-cmd.sh"
mkdir -p "$FIXTURE/extensions/services"
shopt -s nullglob
for source_dir in "$ROOT_DIR/extensions/services"/*; do
    [[ -d "$source_dir" ]] || continue
    target_dir="$FIXTURE/extensions/services/$(basename "$source_dir")"
    mkdir -p "$target_dir"
    for source_file in \
        "$source_dir"/manifest.yaml \
        "$source_dir"/manifest.yml \
        "$source_dir"/manifest.json \
        "$source_dir"/compose*.yaml \
        "$source_dir"/compose*.yaml.disabled; do
        [[ -f "$source_file" ]] || continue
        cp "$source_file" "$target_dir/$(basename "$source_file")"
    done
done
shopt -u nullglob

# Model a canonical installer selection: every built-in is disabled first,
# then only the selected compatible services are re-enabled.
while IFS= read -r -d '' compose_file; do
    mv "$compose_file" "${compose_file}.disabled"
done < <(find "$FIXTURE/extensions/services" -mindepth 2 -maxdepth 2 -type f -name compose.yaml -print0)

enable_service() {
    local service="$1"
    local enabled="$FIXTURE/extensions/services/$service/compose.yaml"
    local disabled="${enabled}.disabled"
    [[ -f "$disabled" ]] || fail "$service has no disabled base compose to enable"
    mv "$disabled" "$enabled"
}

disable_service() {
    local service="$1"
    local enabled="$FIXTURE/extensions/services/$service/compose.yaml"
    local disabled="${enabled}.disabled"
    if [[ -f "$enabled" ]]; then
        mv "$enabled" "$disabled"
    fi
    [[ -f "$disabled" ]] || fail "$service has no disabled base compose"
}

enable_service litellm
enable_service qdrant

# A disabled base must suppress every specialized fragment, even when those
# fragments would otherwise match the current backend, mode, and GPU count.
cat > "$FIXTURE/extensions/services/openclaw/compose.apple.yaml" <<'YAML'
services:
  openclaw:
    environment:
      ODS_DISABLED_OVERLAY: apple
YAML
cat > "$FIXTURE/extensions/services/openclaw/compose.nvidia.yaml" <<'YAML'
services:
  openclaw:
    environment:
      ODS_DISABLED_OVERLAY: nvidia
YAML
cat > "$FIXTURE/extensions/services/openclaw/compose.multigpu-apple.yaml" <<'YAML'
services:
  openclaw:
    environment:
      ODS_DISABLED_OVERLAY: multigpu-apple
YAML
cat > "$FIXTURE/extensions/services/openclaw/compose.multigpu-nvidia.yaml" <<'YAML'
services:
  openclaw:
    environment:
      ODS_DISABLED_OVERLAY: multigpu-nvidia
YAML

write_valid_auth_overlay() {
    cat > "$FIXTURE/$AUTH_REL" <<'YAML'
services:
  open-webui:
    environment:
      OPENAI_API_KEY: "${LITELLM_KEY:?LITELLM_KEY must be set}"
YAML
}

resolve_env() {
    bash "$RESOLVER" \
        --script-dir "$FIXTURE" \
        --tier AP_BASE \
        --gpu-backend apple \
        --gpu-count "${1:-1}" \
        --ods-mode cloud \
        --env | normalize_paths
}

file_list_from_env() {
    sed -n 's/^COMPOSE_FILE_LIST="\(.*\)"$/\1/p'
}

flags_from_env() {
    sed -n 's/^COMPOSE_FLAGS="\(.*\)"$/\1/p'
}

assert_auth_last_once() {
    local file_list="$1"
    local count
    [[ "$file_list" == *"$AUTH_REL" ]] || fail "generated auth overlay is not last: $file_list"
    count="$(tr ',' '\n' <<< "$file_list" | grep -Fxc "$AUTH_REL" || true)"
    [[ "$count" == "1" ]] || fail "generated auth overlay occurred $count times: $file_list"
}

assert_selected_bases() {
    local file_list="$1" expected="$2"
    local actual
    actual="$(tr ',' '\n' <<< "$file_list" \
        | grep -E '^extensions/services/[^/]+/compose\.yaml$' \
        | LC_ALL=C sort \
        | paste -sd ',' -)"
    [[ "$actual" == "$expected" ]] \
        || fail "resolved built-in bases differ: expected '$expected', got '$actual'"
}

# Reject an overlay that would synthesize disabled Hermes as a partial service.
cat > "$FIXTURE/$AUTH_REL" <<'YAML'
services:
  open-webui:
    environment:
      OPENAI_API_KEY: "${LITELLM_KEY:?LITELLM_KEY must be set}"
  hermes:
    environment:
      OPENAI_API_KEY: "${LITELLM_KEY:?LITELLM_KEY must be set}"
YAML
if resolve_env > "$TMP_DIR/invalid.out" 2> "$TMP_DIR/invalid.err"; then
    fail "resolver accepted a generated overlay containing disabled Hermes"
fi
grep -Fq 'may target only the always-present open-webui service' "$TMP_DIR/invalid.err" \
    || fail "resolver did not explain the rejected partial-service overlay"
pass "generated overlays cannot synthesize partial extension services"

write_valid_auth_overlay

# Even an otherwise valid generated overlay must not introduce Open WebUI when
# the selected base stack does not define it.
mv "$FIXTURE/docker-compose.base.yml" "$TMP_DIR/docker-compose.base.yml"
cat > "$FIXTURE/docker-compose.base.yml" <<'YAML'
services:
  dashboard:
    image: example.invalid/dashboard:test
YAML
if resolve_env > "$TMP_DIR/missing-base.out" 2> "$TMP_DIR/missing-base.err"; then
    fail "resolver let a generated overlay create a missing core service"
fi
grep -Fq 'would create partial service(s): open-webui' "$TMP_DIR/missing-base.err" \
    || fail "resolver did not identify the missing generated-overlay target"
mv "$TMP_DIR/docker-compose.base.yml" "$FIXTURE/docker-compose.base.yml"
pass "generated overlays require their target service in the base stack"

disabled_env="$(resolve_env 2)"
disabled_files="$(file_list_from_env <<< "$disabled_env")"
assert_auth_last_once "$disabled_files"
assert_selected_bases "$disabled_files" \
    'extensions/services/litellm/compose.yaml,extensions/services/qdrant/compose.yaml'
if grep -Fq 'extensions/services/openclaw/compose.' <<< "$disabled_files"; then
    fail "disabled OpenClaw contributed a specialized overlay: $disabled_files"
fi
if grep -Fq 'extensions/services/hermes/' <<< "$disabled_files"; then
    fail "disabled Hermes contributed a compose fragment: $disabled_files"
fi
pass "disabled built-ins contribute neither base nor specialized overlays"

stale_profile="$AUTH_REL,docker-compose.base.yml,docker-compose.cloud.yml"
stale_profile_env="$(bash "$RESOLVER" --script-dir "$FIXTURE" --tier AP_BASE \
    --gpu-backend apple --gpu-count 1 --ods-mode cloud \
    --profile-overlays "$stale_profile" --env | normalize_paths)"
assert_auth_last_once "$(file_list_from_env <<< "$stale_profile_env")"
pass "stale profile auth entries are deduplicated and moved last"

if bash "$RESOLVER" --script-dir "$FIXTURE" --tier AP_BASE \
    --gpu-backend apple --gpu-count 1 --ods-mode cloud \
    --profile-overlays "$AUTH_REL" > "$TMP_DIR/auth-only.out" 2> "$TMP_DIR/auth-only.err"; then
    fail "resolver accepted a generated auth overlay as the entire base stack"
fi
grep -Fq 'compose resolution produced no usable base files' "$TMP_DIR/auth-only.err" \
    || fail "resolver did not explain the auth-only profile rejection"
pass "generated auth overlay cannot serve as a primary stack"

# The auth overlay is Apple-cloud-only, even if a stale profile list supplies it.
profile_files="docker-compose.base.yml,docker-compose.cloud.yml,$AUTH_REL"
local_profile="$(bash "$RESOLVER" --script-dir "$FIXTURE" --tier AP_BASE \
    --gpu-backend apple --gpu-count 1 --ods-mode local \
    --profile-overlays "$profile_files" --env | normalize_paths)"
if grep -Fq "$AUTH_REL" <<< "$(file_list_from_env <<< "$local_profile")"; then
    fail "local Apple resolution retained the generated cloud auth overlay"
fi
nonapple_profile="$(bash "$RESOLVER" --script-dir "$FIXTURE" --tier CLOUD \
    --gpu-backend nvidia --gpu-count 1 --ods-mode cloud \
    --profile-overlays "$profile_files" --env | normalize_paths)"
if grep -Fq "$AUTH_REL" <<< "$(file_list_from_env <<< "$nonapple_profile")"; then
    fail "non-Apple cloud resolution retained the generated macOS auth overlay"
fi
pass "generated auth overlay is restricted to Apple cloud mode"

# Exercise the same disabled-base invariant through local/NVIDIA paths, where
# local and multi-GPU overlays are otherwise eligible.
local_nvidia="$(bash "$RESOLVER" --script-dir "$FIXTURE" --tier 2 \
    --gpu-backend nvidia --gpu-count 2 --ods-mode local --env | normalize_paths)"
if grep -Fq 'extensions/services/openclaw/compose.' <<< "$(file_list_from_env <<< "$local_nvidia")"; then
    fail "disabled OpenClaw contributed a local/NVIDIA/multi-GPU overlay"
fi
pass "disabled bases suppress GPU, local, and multi-GPU fragments"

DOCKER_COMPOSE_AVAILABLE=false
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    DOCKER_COMPOSE_AVAILABLE=true
fi

render_state() {
    local state="$1" expected_services="$2" expect_hermes="$3"
    local resolved_env compose_flags render_file services_file actual_services
    local -a compose_args

    resolved_env="$(resolve_env)"
    compose_flags="$(flags_from_env <<< "$resolved_env")"
    read -r -a compose_args <<< "$compose_flags"
    render_file="$TMP_DIR/render-$state.yaml"
    services_file="$TMP_DIR/services-$state.txt"

    (
        cd "$FIXTURE"
        WEBUI_SECRET=resolver-webui-secret \
        LITELLM_KEY=sk-resolver-test \
        HERMES_LLM_BASE_URL=http://litellm:4000/v1 \
        HERMES_LLM_API_KEY=sk-resolver-test \
            docker compose "${compose_args[@]}" config > "$render_file"
        WEBUI_SECRET=resolver-webui-secret \
        LITELLM_KEY=sk-resolver-test \
        HERMES_LLM_BASE_URL=http://litellm:4000/v1 \
        HERMES_LLM_API_KEY=sk-resolver-test \
            docker compose "${compose_args[@]}" config --services \
            | LC_ALL=C sort > "$services_file"
    )

    actual_services="$(paste -sd ',' "$services_file")"
    [[ "$actual_services" == "$expected_services" ]] \
        || fail "$state render services differ: expected '$expected_services', got '$actual_services'"

    "$PYTHON_CMD" - "$render_file" "$expect_hermes" <<'PY'
import pathlib
import sys
import yaml

rendered = yaml.safe_load(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
services = rendered.get("services", {})
if services.get("open-webui", {}).get("environment", {}).get("OPENAI_API_KEY") != "sk-resolver-test":
    raise SystemExit("Open WebUI lost LiteLLM authentication")
expect_hermes = sys.argv[2] == "true"
if expect_hermes:
    if services.get("hermes", {}).get("environment", {}).get("OPENAI_API_KEY") != "sk-resolver-test":
        raise SystemExit("enabled Hermes lost LiteLLM authentication")
elif "hermes" in services:
    raise SystemExit("disabled Hermes was synthesized as a partial service")
PY
}

. "$ROOT_DIR/lib/python-cmd.sh"
PYTHON_CMD="$(ods_detect_python_cmd_with_module yaml)"

if $DOCKER_COMPOSE_AVAILABLE; then
    render_state disabled \
        'dashboard,dashboard-api,litellm,open-webui,qdrant' false
    pass "Hermes-disabled Apple cloud stack renders with exact selected services and auth"
fi

enable_service hermes
enabled_env="$(resolve_env)"
enabled_files="$(file_list_from_env <<< "$enabled_env")"
assert_auth_last_once "$enabled_files"
assert_selected_bases "$enabled_files" \
    'extensions/services/hermes/compose.yaml,extensions/services/litellm/compose.yaml,extensions/services/qdrant/compose.yaml'
if $DOCKER_COMPOSE_AVAILABLE; then
    render_state enabled \
        'dashboard,dashboard-api,hermes,litellm,open-webui,qdrant' true
    pass "Hermes-enabled Apple cloud stack renders without missing client auth"
fi

disable_service hermes
reenabled_env="$(resolve_env)"
reenabled_files="$(file_list_from_env <<< "$reenabled_env")"
assert_selected_bases "$reenabled_files" \
    'extensions/services/litellm/compose.yaml,extensions/services/qdrant/compose.yaml'
if $DOCKER_COMPOSE_AVAILABLE; then
    render_state redisabled \
        'dashboard,dashboard-api,litellm,open-webui,qdrant' false
    pass "Hermes re-disable renders cleanly without a partial service"
else
    echo "[SKIP] docker compose unavailable; resolver selection checks still passed"
fi

echo "[OK] macOS cloud resolver enable/disable contract holds"
