#!/usr/bin/env bash
# scripts/ods-doctor.sh must read .env through lib/safe-env.sh, the reader every
# other lifecycle command uses. Its private loader exported every key verbatim,
# so a legacy `UID=1000` line (still valid for Compose, still present on
# installs that predate ODS_UID) hit Bash's readonly UID and aborted the doctor
# under `set -e` before any report was written.
if [ "${BASH_VERSINFO[0]:-0}" -lt 4 ]; then
    for candidate in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        [[ -x "$candidate" ]] && exec "$candidate" "$0" "$@"
    done
    echo "[FAIL] Bash 4+ is required" >&2
    exit 1
fi
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCTOR="${ODS_DOCTOR_UNDER_TEST:-$ROOT_DIR/scripts/ods-doctor.sh}"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

[[ -f "$DOCTOR" ]] || fail "missing $DOCTOR"
command -v jq >/dev/null 2>&1 || fail "jq is required"
command -v python3 >/dev/null 2>&1 || fail "python3 is required by ods-doctor.sh"

# Minimal install tree: the doctor resolves everything relative to its own
# parent directory, so give it lib/, scripts/, config/, the schema and the
# service manifests, and nothing else.
INSTALL="$TMP_DIR/install"
mkdir -p "$INSTALL/bin" "$INSTALL/extensions/services"
cp -R "$ROOT_DIR/lib" "$ROOT_DIR/scripts" "$ROOT_DIR/config" "$INSTALL/"
cp "$ROOT_DIR/.env.schema.json" "$INSTALL/"
cp "$DOCTOR" "$INSTALL/scripts/ods-doctor.sh"
for manifest in "$ROOT_DIR"/extensions/services/*/manifest.yaml; do
    svc="$(basename "$(dirname "$manifest")")"
    mkdir -p "$INSTALL/extensions/services/$svc"
    cp "$manifest" "$INSTALL/extensions/services/$svc/"
done
# No daemon: every container probe fails fast and the report still gets written.
printf '#!/bin/sh\necho "Cannot connect to the Docker daemon" >&2\nexit 1\n' > "$INSTALL/bin/docker"
chmod +x "$INSTALL/bin/docker"

run_doctor() {
    local report="$1"
    local out="$2"
    (
        cd "$INSTALL" || exit 1
        PATH="$INSTALL/bin:$PATH" "$BASH" scripts/ods-doctor.sh "$report"
    ) >"$out" 2>&1
}

# Case 1: a .env from an install that predates ODS_UID keeps the legacy UID/GID
# keys. Compose accepts them; the doctor must too.
cat > "$INSTALL/.env" <<'EOF'
ODS_MODE=local
GPU_BACKEND=cpu
OLLAMA_PORT=11434
WEBUI_PORT="3000"
UID=1000
GID=1000
EOF
report="$TMP_DIR/legacy-uid.json"
if ! run_doctor "$report" "$TMP_DIR/legacy-uid.out"; then
    fail "doctor aborted on a legacy UID= line: $(grep -m1 -i 'readonly\|error' "$TMP_DIR/legacy-uid.out" | cut -c1-120)"
fi
[[ -f "$report" ]] || fail "doctor wrote no report for the legacy-UID .env"
pass "doctor completes on a .env that still carries UID=/GID="

url="$(jq -r '.runtime.llm_backend.url' "$report")"
[[ "$url" == "http://127.0.0.1:11434" ]] \
    || fail "llama-server probe URL is not built from OLLAMA_PORT (got: $url)"
mode="$(jq -r '.runtime.inference_contract.ods_mode' "$report")"
[[ "$mode" == "local" ]] || fail "ODS_MODE did not reach the report (got: $mode)"
pass "doctor reads ports and mode from .env through the shared reader"

# Case 2: the loader must still be the shared one, not a second copy of it.
# shellcheck disable=SC2016  # the literal $env_file is what we look for
grep -q 'load_env_file "\$env_file"' "$DOCTOR" \
    || fail "ods-doctor.sh does not delegate .env loading to lib/safe-env.sh load_env_file"
pass "doctor delegates .env loading to lib/safe-env.sh"
