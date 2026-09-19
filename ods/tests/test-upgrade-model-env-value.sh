#!/usr/bin/env bash
# upgrade-model.sh must persist the model NAME in .env, not its host path.
#
# start_llm wrote its $model argument straight into LLM_MODEL, and both
# callers (cmd_upgrade, cmd_rollback) passed $model_path — the absolute
# models-dir path on the host. The llama-server container receives LLM_MODEL
# as a model id/name (see .env.example: qwen3.5-9b), so the upgrade always
# wrote a value the stack could not resolve; rollback then persisted the
# previous model's host path the same way.
#
# Run from repo root:  bash ods/tests/test-upgrade-model-env-value.sh
# Or from ods:         bash tests/test-upgrade-model-env-value.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT_DIR/scripts/upgrade-model.sh"

fail() { echo "[FAIL] $*"; exit 1; }
pass() { echo "[PASS] $*"; }

command -v jq >/dev/null 2>&1 || fail "jq is required (upgrade-model.sh prerequisite)"

FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/ods-upgrade-model.XXXXXX")"
trap 'rm -rf "$FIXTURE"' EXIT
ODS_DIR="$FIXTURE/ods"
BIN_DIR="$FIXTURE/bin"
mkdir -p "$ODS_DIR/data/models/old-model" "$ODS_DIR/data/models/new-model" "$BIN_DIR"

printf '{"name":"old-model"}\n' > "$ODS_DIR/data/models/old-model/config.json"
printf '{"name":"new-model"}\n' > "$ODS_DIR/data/models/new-model/config.json"
printf 'LLM_MODEL=old-model\n' > "$ODS_DIR/.env"
printf '{"current":"old-model","previous":""}\n' > "$ODS_DIR/model-state.json"
printf 'services:\n  llama-server:\n    image: example/llama:test\n' > "$ODS_DIR/docker-compose.yml"

# docker stub: compose/config/ps/stop/start/up all succeed quietly.
cat > "$BIN_DIR/docker" <<'SH'
#!/usr/bin/env bash
if [[ "${1:-}" == "compose" ]]; then
    shift
    # drop -f args
    while [[ "${1:-}" == "-f" ]]; do shift 2; done
    if [[ "${1:-}" == "config" ]]; then
        printf 'llama-server\n'
    fi
    exit 0
fi
exit 0
SH
chmod +x "$BIN_DIR/docker"

# curl stub: /health -> 200, /v1/models -> {"data": [...]}
cat > "$BIN_DIR/curl" <<'SH'
#!/usr/bin/env bash
for arg in "$@"; do
    case "$arg" in
        */health) printf '200'; exit 0 ;;
        */v1/models) printf '{"data":[{"id":"new-model"}]}'; exit 0 ;;
    esac
done
exit 0
SH
chmod +x "$BIN_DIR/curl"

run_upgrade() {
    ODS_DIR="$ODS_DIR" MODELS_DIR="$ODS_DIR/data/models" \
        PATH="$BIN_DIR:$PATH" bash "$SCRIPT" "$@" 2>&1
}

echo "Test 1: upgrade writes the model NAME into LLM_MODEL"
out="$(run_upgrade new-model)" || { echo "$out"; fail "upgrade failed"; }
env_value="$(grep '^LLM_MODEL=' "$ODS_DIR/.env" | cut -d= -f2)"
[[ "$env_value" == "new-model" ]] \
    || { echo "$out"; fail "LLM_MODEL='$env_value' — expected the model name, not a path"; }
pass "upgrade persists LLM_MODEL=new-model"

echo "Test 2: rollback writes the previous model NAME, not a path"
printf '{"current":"new-model","previous":"old-model"}\n' > "$ODS_DIR/model-state.json"
cp "$ODS_DIR/model-state.json" "$ODS_DIR/model-state.backup.json"
printf 'LLM_MODEL=new-model\n' > "$ODS_DIR/.env"
out="$(run_upgrade --rollback)" || { echo "$out"; fail "rollback failed"; }
env_value="$(grep '^LLM_MODEL=' "$ODS_DIR/.env" | cut -d= -f2)"
[[ "$env_value" == "old-model" ]] \
    || { echo "$out"; fail "rollback wrote LLM_MODEL='$env_value' — expected model name"; }
pass "rollback persists LLM_MODEL=old-model"

echo "Test 3: .env contents stay atomically consistent (single LLM_MODEL line)"
[[ "$(grep -c '^LLM_MODEL=' "$ODS_DIR/.env")" -eq 1 ]] \
    || fail "duplicate LLM_MODEL entries after rewrite"
pass "exactly one LLM_MODEL entry"

echo ""
echo "All upgrade-model .env value tests passed."
