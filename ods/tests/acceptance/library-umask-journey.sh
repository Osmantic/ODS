#!/usr/bin/env bash
# Disposable Docker Engine journey for exact #6975 under caller umask 077.
set -euo pipefail

product="${ODS_ACCEPTANCE_PRODUCT_ROOT:?product checkout is required}"
harness="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
expected=8746cb0147b296b73c9acbcd58c0acadce3d57b5
audit_root="${RUNNER_TEMP:?runner temp is required}/ods-library-umask-acceptance"
export INSTALL_DIR="$audit_root/install"
export LOG_FILE="$audit_root/install.log"
key_file="$audit_root/mock.key"
mock_log="$audit_root/mock.log"
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
    if [[ -f "$LOG_FILE" ]]; then
        grep -E 'Phase 06 step:|PermissionError|Could not secure the installed extension library' "$LOG_FILE" \
            | tail -n 25 >&2 || true
    fi
    docker ps --format '{{.Names}} {{.Status}}' >&2 || true
    exit 1
}

run_installer() {
    if ! (umask 077; cd "$product" && timeout 2400s bash install-core.sh \
        --gateway-only --non-interactive --skip-docker --no-bootstrap \
        --external-llm-url 'http://127.0.0.1:18080' \
        --external-llm-provider openai-compatible \
        --external-llm-model ods-acceptance-mock \
        --external-llm-key-file "$key_file") >>"$LOG_FILE" 2>&1; then
        fail 'gateway installer failed under umask 077'
    fi
}

check_library() {
    local label="$1" result="$audit_root/$1.json"
    local attempt
    for attempt in {1..30}; do
        python3 "$harness/portal-diagnostics.py" "$INSTALL_DIR/.env" actual-budget >"$result"
        if python3 - "$result" <<'PY'
import json
import sys

value = json.load(open(sys.argv[1], encoding="utf-8"))
if not value["credentialShapeValid"] or not value["portValid"]:
    raise SystemExit(1)
for name in ("health", "catalog", "detail", "plan"):
    if value[name].get("httpStatus") != 200:
        raise SystemExit(1)
PY
        then
            printf 'PASS: %s authenticated Extensions detail and install-plan returned HTTP 200\n' "$label"
            return 0
        fi
        sleep 5
    done
    cat "$result" >&2
    fail "$label Extensions detail or install-plan did not become readable"
}

[[ "${GITHUB_ACTIONS:-}" == true ]] || fail 'refusing non-disposable host'
[[ "$RUNNER_TEMP" == /* && "$INSTALL_DIR" == "$RUNNER_TEMP"/* ]] || fail 'install path is outside runner temp'
[[ "$(git -C "$product" rev-parse HEAD)" == "$expected" ]] || fail 'product checkout is not the pinned PR head'
[[ "$(cat /proc/1/comm)" == systemd ]] || fail 'runner is not a systemd host'
docker info >/dev/null || fail 'isolated Docker Engine unavailable'
[[ ! -e "$INSTALL_DIR" ]] || fail 'fresh install path is not empty'
if docker ps -a --format '{{.Names}}' | grep -Eq '^ods-'; then
    fail 'pre-existing ODS containers on runner'
fi

umask 077
mkdir -p "$audit_root"
python3 - "$key_file" <<'PY'
import secrets
import sys

with open(sys.argv[1], "w", encoding="ascii") as stream:
    stream.write("mock-" + secrets.token_hex(24))
PY
chmod 600 "$key_file"
python3 "$harness/mock-openai-upstream.py" --key-file "$key_file" \
    --port 18080 >"$mock_log" 2>&1 &
mock_pid=$!
for attempt in {1..30}; do
    curl -fsS --max-time 2 http://127.0.0.1:18080/healthz >/dev/null 2>&1 && break
    sleep 1
done
curl -fsS --max-time 2 http://127.0.0.1:18080/healthz >/dev/null \
    || fail 'mock upstream did not start'

printf 'Installing exact #6975 product head %s under umask 077\n' "$expected"
run_installer
data_mode="$(stat -c %a "$INSTALL_DIR/data")"
(( (8#$data_mode & 1) == 1 && (8#$data_mode & 2) == 0 )) \
    || fail 'data parent traversal mode is wrong'
[[ "$(stat -c %a "$INSTALL_DIR/data/extensions-library")" == 755 ]] || fail 'Library root mode is wrong'
[[ "$(stat -c %a "$INSTALL_DIR/data/extensions-library/actual-budget/manifest.yaml")" == 644 ]] \
    || fail 'copied Library manifest is unreadable'
[[ "$(stat -c %a "$key_file")" == 600 ]] || fail 'private upstream key mode changed'
check_library fresh

custom="$INSTALL_DIR/data/extensions-library/owner-private"
mkdir -p "$custom"
printf 'retained private\n' >"$custom/notes.txt"
chmod 700 "$custom"
chmod 600 "$custom/notes.txt"
sentinel="$(sha256sum "$custom/notes.txt" | cut -d' ' -f1)"
run_installer
[[ "$(stat -c %a "$custom")" == 700 ]] || fail 'rerun widened a custom directory'
[[ "$(stat -c %a "$custom/notes.txt")" == 600 ]] || fail 'rerun widened a custom file'
[[ "$(sha256sum "$custom/notes.txt" | cut -d' ' -f1)" == "$sentinel" ]] \
    || fail 'rerun changed retained custom data'
check_library rerun
printf 'PASS: restrictive-umask install and rerun kept the Library readable and retained data private\n'
