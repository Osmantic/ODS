#!/usr/bin/env bash
# scripts/ods-test.sh must run every section and print its summary even when
# a check fails, and `--json` must leave only the JSON document on stdout.
#
# Section functions returned 1 after a failed check; called as plain
# statements under `set -e`, that aborted the whole run at the first failing
# section (no summary in text mode, no document at all in --json mode), and
# the section banners were printed regardless of --json.
if [ "${BASH_VERSINFO[0]:-0}" -lt 4 ]; then
    for candidate in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        [[ -x "$candidate" ]] && exec "$candidate" "$0" "$@"
    done
    echo "[FAIL] Bash 4+ is required (service registry)" >&2
    exit 1
fi
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${ODS_TEST_UNDER_TEST:-$ROOT_DIR/scripts/ods-test.sh}"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

[[ -f "$TARGET" ]] || fail "missing $TARGET"
command -v python3 >/dev/null 2>&1 || fail "python3 is required"

# The script resolves lib/ and the service registry from its own parent
# directory, so run it from a copy of the checkout's scripts/ directory.
INSTALL="$TMP_DIR/install"
mkdir -p "$INSTALL/scripts" "$INSTALL/bin" "$INSTALL/extensions/services"
cp -R "$ROOT_DIR/lib" "$INSTALL/"
cp "$TARGET" "$INSTALL/scripts/ods-test.sh"
for manifest in "$ROOT_DIR"/extensions/services/*/manifest.yaml; do
    svc="$(basename "$(dirname "$manifest")")"
    mkdir -p "$INSTALL/extensions/services/$svc"
    cp "$manifest" "$INSTALL/extensions/services/$svc/"
done
# Every probe targets a closed loopback port and fails immediately.
cat > "$INSTALL/.env" <<'EOF'
OLLAMA_PORT=1
WEBUI_PORT=1
WHISPER_PORT=1
TTS_PORT=1
EMBEDDINGS_PORT=1
SHIELD_PORT=1
LIVEKIT_PORT=1
EOF
printf '#!/bin/sh\necho "Cannot connect to the Docker daemon" >&2\nexit 1\n' > "$INSTALL/bin/docker"
chmod +x "$INSTALL/bin/docker"

run_target() {
    (
        cd "$INSTALL" || exit 1
        ODS_DIR="$INSTALL" ENV_FILE="$INSTALL/.env" PATH="$INSTALL/bin:$PATH" \
            "$BASH" scripts/ods-test.sh --quick "$@"
    )
}

# Case 1: --json with failing checks still yields one JSON document that
# covers every section.
rc=0
run_target --json >"$TMP_DIR/out.json" 2>"$TMP_DIR/err.txt" || rc=$?
[[ "$rc" -eq 1 ]] || fail "--json with failing checks should exit 1, got $rc (stderr: $(head -n 2 "$TMP_DIR/err.txt" | tr '\n' ' '))"
python3 - "$TMP_DIR/out.json" <<'PY' || fail "--json stdout is not a single JSON document; first line: $(head -n 1 "$TMP_DIR/out.json")"
import json, sys
doc = json.load(open(sys.argv[1]))
names = [r["name"] for r in doc["results"]]
assert doc["summary"]["failed"] > 0, doc["summary"]
assert "LLM Health" in names, names
assert "Privacy Shield Health" in names and "LiveKit Health" in names, names  # sections after the first failure
PY
pass "--json stdout is one JSON document covering sections after the first failed check"

if grep -q '^> ' "$TMP_DIR/out.json"; then
    fail "section banners leaked into --json stdout"
fi
pass "no section banners on stdout in --json mode"

# Case 2: text mode reaches the summary after a failed section.
rc=0
run_target >"$TMP_DIR/out.txt" 2>&1 || rc=$?
[[ "$rc" -eq 1 ]] || fail "text mode with failing checks should exit 1, got $rc"
grep -q '> Privacy Shield M3' "$TMP_DIR/out.txt" || fail "text mode stopped before the Privacy Shield section"
grep -qE 'Passed:.*Failed:' "$TMP_DIR/out.txt" || fail "text mode printed no summary line"
pass "text mode runs every section and prints the summary"

# Non-quick execution must also finish when inference curl exits nonzero.
run_full() {
    (
        cd "$INSTALL" || exit 1
        ODS_DIR="$INSTALL" ENV_FILE="$INSTALL/.env" PATH="$INSTALL/bin:$PATH" \
            "$BASH" scripts/ods-test.sh --json
    )
}
assert_full_report() {
    local label="$1" expected="$2" rc=0
    run_full >"$TMP_DIR/full.json" 2>"$TMP_DIR/full.err" || rc=$?
    [[ "$rc" -eq 1 ]] || fail "$label should exit 1, got $rc"
    python3 - "$TMP_DIR/full.json" "$expected" <<'PY' || fail "$label lost its failure report"
import json, sys
with open(sys.argv[1]) as stream:
    doc = json.load(stream)
results = {r['name']: r for r in doc['results']}
assert results[sys.argv[2]]['status'] == 'fail', results
assert 'LiveKit Health' in results, results
assert doc['summary']['failed'] > 0, doc
PY
    pass "$label records failure and reaches the last section"
}
assert_full_report "non-quick inference transport failure" "Tool Calling"

# A daemon can answer info then fail ps; an installed GPU driver may be broken.
printf '#!/bin/sh\n[ "$1" = info ] && exit 0\nexit 1\n' > "$INSTALL/bin/docker"
assert_full_report "Docker listing failure" "Running Containers"
printf '#!/bin/sh\nexit 9\n' > "$INSTALL/bin/nvidia-smi"
chmod +x "$INSTALL/bin/nvidia-smi"
assert_full_report "GPU command failure" "NVIDIA GPU"
printf '#!/bin/sh\necho "Test GPU, 0 MiB, 0 MiB, 0"\n' > "$INSTALL/bin/nvidia-smi"
assert_full_report "invalid GPU memory capacity" "GPU Memory"
