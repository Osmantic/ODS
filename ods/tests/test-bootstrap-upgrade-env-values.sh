#!/usr/bin/env bash
# Regression coverage for bootstrap-upgrade .env reads used by model promotion
# and Windows Lemonade rollback.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$ROOT_DIR/scripts/bootstrap-upgrade.sh"
FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/ods-bootstrap-env.XXXXXX")"
trap 'rm -rf "$FIXTURE"' EXIT

extract_function() {
    local name="$1"
    awk -v signature="$name() {" '
        $0 == signature { capture = 1 }
        capture { print }
        capture && $0 == "}" { exit }
    ' "$TARGET"
}

eval "$(extract_function read_env_file_value)"
eval "$(extract_function read_env_value)"
eval "$(extract_function snapshot_env_value)"

ENV_FILE="$FIXTURE/.env"
cat > "$ENV_FILE" <<'EOF'
LLM_MODEL=stale-model
TOKEN=left=middle=right
QUOTED="outer=value"
INNER_QUOTE=abc"def
LLM_MODEL=active-model
EOF

[[ "$(read_env_value LLM_MODEL)" == "active-model" ]] \
    || { echo "FAIL: active env did not use the last duplicate value" >&2; exit 1; }
[[ "$(read_env_value TOKEN)" == "left=middle=right" ]] \
    || { echo "FAIL: active env truncated equals signs" >&2; exit 1; }
[[ "$(read_env_value QUOTED)" == "outer=value" ]] \
    || { echo "FAIL: active env did not strip matching outer quotes" >&2; exit 1; }
[[ "$(read_env_value INNER_QUOTE)" == 'abc"def' ]] \
    || { echo "FAIL: active env removed an interior quote" >&2; exit 1; }

ACTIVE_CONFIG_SNAPSHOT_DIR="$FIXTURE/snapshot"
mkdir -p "$ACTIVE_CONFIG_SNAPSHOT_DIR"
printf 'GGUF_FILE=old.gguf\r\nGGUF_FILE="restored.gguf"\r\n' \
    > "$ACTIVE_CONFIG_SNAPSHOT_DIR/env"

[[ "$(snapshot_env_value GGUF_FILE)" == "restored.gguf" ]] \
    || { echo "FAIL: snapshot env did not use and normalize the last value" >&2; exit 1; }

echo "PASS: bootstrap upgrade reads active and snapshot env values safely"
