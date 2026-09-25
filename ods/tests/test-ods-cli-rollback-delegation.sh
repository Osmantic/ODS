#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

INSTALL_DIR="$TMP_DIR/install"
TRACE_FILE="$TMP_DIR/rollback.trace"
mkdir -p "$INSTALL_DIR"
touch "$INSTALL_DIR/docker-compose.base.yml"

cat > "$INSTALL_DIR/ods-update.sh" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" > "$ROLLBACK_TRACE_FILE"
SH
chmod +x "$INSTALL_DIR/ods-update.sh"

run_rollback() {
    local output_file="$1"
    shift
    printf 'y\n' | ODS_HOME="$INSTALL_DIR" ROLLBACK_TRACE_FILE="$TRACE_FILE" \
        bash "$ROOT_DIR/ods-cli" rollback "$@" >"$output_file" 2>&1
}

run_rollback "$TMP_DIR/latest.out"
[[ "$(cat "$TRACE_FILE")" == "rollback" ]] || {
    cat "$TMP_DIR/latest.out"
    echo "FAIL: ods rollback did not delegate latest-snapshot recovery to ods-update.sh" >&2
    exit 1
}

run_rollback "$TMP_DIR/target.out" "20260901-120000"
[[ "$(cat "$TRACE_FILE")" == "rollback 20260901-120000" ]] || {
    cat "$TMP_DIR/target.out"
    echo "FAIL: ods rollback did not preserve an explicit snapshot target" >&2
    exit 1
}

echo "PASS: ods rollback delegates the complete transaction to ods-update.sh"
