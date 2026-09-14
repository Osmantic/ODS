#!/usr/bin/env bash
# Phase 5D3 exact-candidate source update admission contract.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

fail() { echo "[FAIL] $*"; exit 1; }
pass() { echo "[PASS] $*"; }

for required in git jq; do
    command -v "$required" >/dev/null 2>&1 || fail "$required is required"
done
command -v python3 >/dev/null 2>&1 || command -v python >/dev/null 2>&1 \
    || fail "python3 or python is required"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

REMOTE="$TMP/remote.git"
SEED="$TMP/seed"
BIN_DIR="$TMP/bin"
mkdir -p "$SEED/ods/lib" "$SEED/ods/scripts" "$SEED/ods/config" "$BIN_DIR"

git init -q --bare "$REMOTE"
git init -q "$SEED"
git -C "$SEED" checkout -q -b main
git -C "$SEED" config user.name "ODS Update Contract"
git -C "$SEED" config user.email "ods-update-contract@example.invalid"
git -C "$SEED" config core.autocrlf false

cp "$ROOT_DIR/ods-update.sh" "$SEED/ods/ods-update.sh"
cp "$ROOT_DIR/lib/python-cmd.sh" "$SEED/ods/lib/python-cmd.sh"
chmod +x "$SEED/ods/ods-update.sh"

cat > "$SEED/ods/lib/update-snapshots.sh" <<'SH'
snapshot_pre_update() {
    printf '%s\n' snapshot >> "${UPDATE_EVENT_LOG:?}"
    local target="${INSTALL_DIR}/data/backups/pre-update-$1"
    mkdir -p "$target"
    printf '%s\n' "$target"
}

_restore_snapshot() {
    printf '%s\n' restore >> "${UPDATE_EVENT_LOG:?}"
    return 0
}
SH

cat > "$SEED/ods/scripts/assess-extension-update.py" <<'PY'
#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--install-dir", required=True)
parser.add_argument("--candidate-dir", required=True)
parser.add_argument("--candidate-revision", required=True)
args = parser.parse_args()

candidate = Path(args.candidate_dir)
manifest = json.loads((candidate / "manifest.json").read_text(encoding="utf-8"))
marker = manifest["ods_version"]
with open(os.environ["PREFLIGHT_LOG"], "a", encoding="utf-8") as handle:
    handle.write(
        json.dumps(
            {
                "revision": args.candidate_revision,
                "marker": marker,
                "archiveProbe": manifest.get("archive_probe"),
            }
        )
        + "\n"
    )
with open(os.environ["UPDATE_EVENT_LOG"], "a", encoding="utf-8") as handle:
    handle.write("preflight\n")

if os.environ.get("REMOVE_REMOTE_AFTER_PREFLIGHT") == "1":
    Path(os.environ["REMOTE_PATH"]).rename(os.environ["REMOTE_PATH"] + ".offline")

status = int(os.environ.get("PREFLIGHT_EXIT", "0"))
if status == 12:
    result = {
        "schema": "ods.extensions.update-preflight-error.v1",
        "error": {"code": "fixture-invalid", "details": {}},
    }
else:
    result = {
        "schema": "ods.extensions.update-preflight.v1",
        "candidateSourceRevision": args.candidate_revision,
        "preflightHash": "a" * 64,
        "assessmentEnvelope": {
            "assessment": {
                "canUpdate": status != 11,
                "requiresExtensionPlan": status == 10,
            }
        },
    }
print(json.dumps(result, sort_keys=True, separators=(",", ":")))
raise SystemExit(status)
PY
chmod +x "$SEED/ods/scripts/assess-extension-update.py"

cat > "$SEED/ods/manifest.json" <<'EOF'
{"ods_version":"1.0.0","release":{"version":"1.0.0"}}
EOF
cat > "$SEED/ods/config/extensions-catalog.json" <<'EOF'
{"catalog_revision":"base","extensions":[]}
EOF
cat > "$SEED/ods/docker-compose.base.yml" <<'EOF'
services:
  dashboard-api:
    image: example/dashboard-api:test
EOF
printf '%s\n' base > "$SEED/ods/candidate-marker.txt"

git -C "$SEED" add ods
git -C "$SEED" commit -q -m "base"
BASE_REVISION=$(git -C "$SEED" rev-parse HEAD)
git -C "$SEED" remote add origin "$REMOTE"
git -C "$SEED" push -q -u origin main
git -C "$SEED" push -q origin "${BASE_REVISION}:refs/heads/public-beta"
git -C "$REMOTE" symbolic-ref HEAD refs/heads/main

# Clone each fixture before the candidate exists so a rejected preflight can
# prove the installed object database was not changed by candidate resolution.
for name in blocked-10 blocked-11 blocked-12 blocked-42 beta legacy ready; do
    mkdir -p "$TMP/$name"
    git clone -q "$REMOTE" "$TMP/$name/repository"
done
git -C "$TMP/beta/repository" checkout -q -b public-beta --track origin/public-beta

cat > "$SEED/ods/manifest.json" <<'EOF'
{"ods_version":"1.1.0","release":{"version":"1.1.0"},"archive_probe":"$Format:%H$"}
EOF
cat > "$SEED/ods/config/extensions-catalog.json" <<'EOF'
{"catalog_revision":"candidate","extensions":[]}
EOF
printf '%s\n' candidate > "$SEED/ods/candidate-marker.txt"
cat > "$SEED/.gitattributes" <<'EOF'
ods/manifest.json export-subst
EOF
git -C "$SEED" add ods .gitattributes
git -C "$SEED" commit -q -m "candidate"
CANDIDATE_REVISION=$(git -C "$SEED" rev-parse HEAD)
git -C "$SEED" push -q origin main

git -C "$SEED" checkout -q -b public-beta "$BASE_REVISION"
cat > "$SEED/ods/manifest.json" <<'EOF'
{"ods_version":"1.0.5","release":{"version":"1.0.5"}}
EOF
cat > "$SEED/ods/config/extensions-catalog.json" <<'EOF'
{"catalog_revision":"public-beta-candidate","extensions":[]}
EOF
printf '%s\n' public-beta-candidate > "$SEED/ods/candidate-marker.txt"
git -C "$SEED" add ods
git -C "$SEED" commit -q -m "public beta candidate"
BETA_REVISION=$(git -C "$SEED" rev-parse HEAD)
git -C "$SEED" push -q origin public-beta
git -C "$SEED" checkout -q main

cat > "$BIN_DIR/docker" <<'SH'
#!/usr/bin/env bash
printf 'docker %s\n' "$*" >> "${UPDATE_EVENT_LOG:?}"
if [[ "${1:-}" == "info" ]]; then
    exit 0
fi
if [[ "${1:-}" == "compose" && "${2:-}" == "version" ]]; then
    exit 0
fi
if [[ "${1:-}" == "compose" ]]; then
    shift
fi
case " $* " in
    *" ps --services "*)
        printf '%s\n' dashboard-api
        ;;
    *" ps --format json "*)
        printf '%s\n' '{"State":"running"}'
        ;;
esac
exit 0
SH
chmod +x "$BIN_DIR/docker"

cat > "$BIN_DIR/curl" <<'SH'
#!/usr/bin/env bash
exit 0
SH
chmod +x "$BIN_DIR/curl"

prepare_runtime_state() {
    local name="$1" profile="$2"
    local install="$TMP/$name/repository/ods"
    mkdir -p \
        "$install/data/assistant-first/desired-state" \
        "$install/data/backups" \
        "$TMP/$name/runtime-tmp"
    cat > "$install/.env" <<EOF
ODS_INSTALL_PROFILE=$profile
ODS_MODE=local
GPU_BACKEND=cpu
GPU_COUNT=1
TIER=1
DASHBOARD_API_PORT=3002
OLLAMA_PORT=8080
EOF
    printf '%s\n' '{"version":"1.0.0"}' > "$install/.version"
    printf '%s\n' \
        '{"schema":"ods.extensions.lockfile-envelope.v1","fixture":true}' \
        > "$install/data/assistant-first/desired-state/extensions.lock.json"
    printf '%s\n' '-f docker-compose.base.yml' > "$install/.compose-flags"
}

run_update() {
    local name="$1" status="$2"
    local install="$TMP/$name/repository/ods"
    local output="$TMP/$name/update.out"
    set +e
    HOME="$TMP/$name/home" \
    TMPDIR="$TMP/$name/runtime-tmp" \
    PATH="$BIN_DIR:$PATH" \
    PREFLIGHT_EXIT="$status" \
    PREFLIGHT_LOG="$TMP/$name/preflight.log" \
    UPDATE_EVENT_LOG="$TMP/$name/events.log" \
    bash "$install/ods-update.sh" update > "$output" 2>&1
    RUN_STATUS=$?
    set -e
}

for status in 10 11 12 42; do
    name="blocked-$status"
    prepare_runtime_state "$name" assistant-first
    install="$TMP/$name/repository/ods"
    if git -C "$install" cat-file -e "${CANDIDATE_REVISION}^{commit}" 2>/dev/null; then
        fail "fixture $name already contains the candidate object"
    fi
    run_update "$name" "$status"
    [[ "$RUN_STATUS" -ne 0 ]] || fail "preflight status $status did not block update"
    [[ "$(git -C "$install" rev-parse HEAD)" == "$BASE_REVISION" ]] \
        || fail "preflight status $status changed installed HEAD"
    if git -C "$install" cat-file -e "${CANDIDATE_REVISION}^{commit}" 2>/dev/null; then
        fail "preflight status $status imported candidate objects before approval"
    fi
    [[ -f "$TMP/$name/events.log" ]] || {
        cat "$TMP/$name/update.out"
        fail "preflight status $status did not reach the candidate gate"
    }
    [[ "$(sed -n '1p' "$TMP/$name/events.log")" == preflight ]] \
        || fail "preflight status $status did not run the candidate gate first"
    [[ "$(wc -l < "$TMP/$name/events.log" | tr -d ' ')" == 1 ]] \
        || fail "preflight status $status reached snapshot or Docker mutation"
    [[ "$(jq -r '.revision' "$TMP/$name/preflight.log")" == "$CANDIDATE_REVISION" ]] \
        || fail "preflight status $status was not bound to the exact candidate"
    [[ "$(jq -r '.marker' "$TMP/$name/preflight.log")" == 1.1.0 ]] \
        || fail "preflight status $status did not inspect the candidate tree"
    if find "$TMP/$name/runtime-tmp" -mindepth 1 -maxdepth 1 \
        -type d -name 'ods-update-candidate.*' | grep -q .; then
        fail "preflight status $status left a candidate workspace"
    fi
done
pass "all non-ready preflight states fail before installed checkout and runtime mutation"

prepare_runtime_state beta assistant-first
[[ "$(git -C "$TMP/beta/repository/ods" rev-parse \
    --abbrev-ref --symbolic-full-name '@{upstream}')" == origin/public-beta ]] \
    || fail "public-beta fixture lost its configured upstream"
run_update beta 0
[[ "$RUN_STATUS" -eq 0 ]] || {
    cat "$TMP/beta/update.out"
    fail "configured public-beta upstream update failed"
}
beta_actual_revision=$(git -C "$TMP/beta/repository/ods" rev-parse HEAD)
if [[ "$beta_actual_revision" != "$BETA_REVISION" ]]; then
    printf 'expected public-beta revision: %s\n' "$BETA_REVISION"
    printf 'actual installed revision: %s\n' "$beta_actual_revision"
    cat "$TMP/beta/update.out"
    fail "configured public-beta checkout crossed to the main branch"
fi
[[ "$(jq -r '.revision' "$TMP/beta/preflight.log")" == "$BETA_REVISION" ]] \
    || fail "public-beta preflight was not bound to its configured upstream"
[[ "$(jq -r '.marker' "$TMP/beta/preflight.log")" == 1.0.5 ]] \
    || fail "public-beta update inspected the wrong candidate subtree"
pass "configured public-beta checkouts remain on their exact upstream channel"

prepare_runtime_state legacy full
run_update legacy 42
[[ "$RUN_STATUS" -eq 0 ]] || {
    cat "$TMP/legacy/update.out"
    fail "legacy profile update failed"
}
[[ "$(git -C "$TMP/legacy/repository/ods" rev-parse HEAD)" == \
    "$CANDIDATE_REVISION" ]] || fail "legacy update path did not retain git pull behavior"
[[ ! -e "$TMP/legacy/preflight.log" ]] \
    || fail "legacy profile entered the Assistant First preflight"
[[ "$(sed -n '1p' "$TMP/legacy/events.log")" == snapshot ]] \
    || fail "legacy profile no longer snapshots before its established pull path"
pass "Full/Core/Custom source update behavior remains on the legacy path"

prepare_runtime_state ready assistant-first
ready_install="$TMP/ready/repository/ods"
set +e
HOME="$TMP/ready/home" \
TMPDIR="$TMP/ready/runtime-tmp" \
PATH="$BIN_DIR:$PATH" \
PREFLIGHT_EXIT=0 \
PREFLIGHT_LOG="$TMP/ready/preflight.log" \
UPDATE_EVENT_LOG="$TMP/ready/events.log" \
REMOVE_REMOTE_AFTER_PREFLIGHT=1 \
REMOTE_PATH="$REMOTE" \
bash "$ready_install/ods-update.sh" update > "$TMP/ready/update.out" 2>&1
ready_status=$?
set -e
[[ "$ready_status" -eq 0 ]] || {
    cat "$TMP/ready/update.out"
    fail "ready exact-candidate update failed after the network disappeared"
}
[[ "$(git -C "$ready_install" rev-parse HEAD)" == "$CANDIDATE_REVISION" ]] \
    || fail "ready update did not apply the preflighted exact candidate"
[[ "$(jq -r '.marker' "$TMP/ready/preflight.log")" == 1.1.0 ]] \
    || fail "ready update did not inspect the exact candidate manifest blob"
[[ "$(jq -r '.archiveProbe' "$TMP/ready/preflight.log")" == '$Format:%H$' ]] \
    || fail "candidate attributes transformed the bytes assessed by preflight"
ready_first=$(sed -n '1p' "$TMP/ready/events.log" | tr -d '\r')
ready_second=$(sed -n '2p' "$TMP/ready/events.log" | tr -d '\r')
[[ "$ready_first" == preflight && "$ready_second" == snapshot ]] \
    || fail "ready update did not complete preflight before snapshot"
grep -q '^docker .* down --remove-orphans$' "$TMP/ready/events.log" \
    || fail "ready update did not reach runtime restart after exact checkout"
[[ -d "${REMOTE}.offline" ]] \
    || fail "network disappearance fixture did not run after preflight"
if find "$TMP/ready/runtime-tmp" -mindepth 1 -maxdepth 1 \
    -type d -name 'ods-update-candidate.*' | grep -q .; then
    fail "successful update left a candidate workspace"
fi
pass "ready update applies only the preflighted object without a second network fetch"

echo "[PASS] Phase 5D3 Assistant First source update admission contract"
