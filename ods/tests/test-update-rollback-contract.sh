#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ODS_BACKUP="$ROOT_DIR/ods-backup.sh"
ODS_RESTORE="$ROOT_DIR/ods-restore.sh"
OUT_DIR="$ROOT_DIR/artifacts/update-rollback"

fail() { echo "[FAIL] $*"; exit 1; }
pass() { echo "[PASS] $*"; }

command -v jq >/dev/null 2>&1 || fail "jq is required"
command -v rsync >/dev/null 2>&1 || fail "rsync is required"
command -v sha256sum >/dev/null 2>&1 || fail "sha256sum is required"

[[ -x "$ODS_BACKUP" ]] || fail "ods-backup.sh is not executable"
[[ -x "$ODS_RESTORE" ]] || fail "ods-restore.sh is not executable"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

SRC="$TMP/ods"
mkdir -p "$SRC/lib" "$SRC/config" "$SRC/data/open-webui" "$SRC/data/hermes"
cp "$ROOT_DIR/lib/rsync.sh" "$SRC/lib/rsync.sh"

cat > "$SRC/.version" <<'EOF'
1.0.0
EOF
cat > "$SRC/.env" <<'EOF'
ODS_MODE=local
LLM_MODEL=baseline-model
SECRET_TOKEN=baseline-secret
EOF
cat > "$SRC/docker-compose.yml" <<'EOF'
services:
  placeholder:
    image: busybox:1.36
EOF
cat > "$SRC/config/settings.json" <<'EOF'
{"mode":"baseline","model":"baseline-model"}
EOF
cat > "$SRC/data/open-webui/data.txt" <<'EOF'
baseline-user-data
EOF
cat > "$SRC/data/hermes/config.yaml" <<'EOF'
model:
  default: "baseline-model"
EOF

baseline_hash="$(sha256sum "$SRC/.env" "$SRC/config/settings.json" "$SRC/data/open-webui/data.txt" | sha256sum | awk '{print $1}')"

ODS_DIR="$SRC" RETENTION_COUNT=10 bash "$ODS_BACKUP" --type full >/dev/null 2>&1
BACKUP_ID="$(find "$SRC/.backups" -mindepth 1 -maxdepth 1 -type d -exec basename {} \; | sort | tail -n 1)"
[[ -n "$BACKUP_ID" ]] || fail "backup ID was not created"
pass "backup created before update mutation: $BACKUP_ID"

cat > "$SRC/.version" <<'EOF'
2.0.0
EOF
cat > "$SRC/.env" <<'EOF'
ODS_MODE=lemonade
LLM_MODEL=updated-model
SECRET_TOKEN=updated-secret
EOF
cat > "$SRC/config/settings.json" <<'EOF'
{"mode":"updated","model":"updated-model"}
EOF
cat > "$SRC/data/open-webui/data.txt" <<'EOF'
updated-user-data
EOF

updated_hash="$(sha256sum "$SRC/.env" "$SRC/config/settings.json" "$SRC/data/open-webui/data.txt" | sha256sum | awk '{print $1}')"
[[ "$updated_hash" != "$baseline_hash" ]] || fail "update mutation did not change the test state"
pass "update mutation changed config and data"

ODS_DIR="$SRC" bash "$ODS_RESTORE" -f "$BACKUP_ID" >/dev/null 2>&1

[[ "$(cat "$SRC/.version")" == "1.0.0" ]] || fail ".version did not roll back"
grep -q "LLM_MODEL=baseline-model" "$SRC/.env" || fail ".env model did not roll back"
grep -q "SECRET_TOKEN=baseline-secret" "$SRC/.env" || fail ".env secret did not roll back"
grep -q '"mode":"baseline"' "$SRC/config/settings.json" || fail "config did not roll back"
grep -q "baseline-user-data" "$SRC/data/open-webui/data.txt" || fail "user data did not roll back"

rollback_hash="$(sha256sum "$SRC/.env" "$SRC/config/settings.json" "$SRC/data/open-webui/data.txt" | sha256sum | awk '{print $1}')"
[[ "$rollback_hash" == "$baseline_hash" ]] || fail "rollback hash does not match baseline"
pass "rollback restored baseline hash"

mkdir -p "$OUT_DIR"
jq -n \
  --arg backup_id "$BACKUP_ID" \
  --arg baseline_hash "$baseline_hash" \
  --arg updated_hash "$updated_hash" \
  --arg rollback_hash "$rollback_hash" \
  '{
    version: "1",
    scenario: "install-backup-update-rollback",
    backup_id: $backup_id,
    checks: {
      backup_created: true,
      update_changed_state: ($baseline_hash != $updated_hash),
      rollback_matches_baseline: ($baseline_hash == $rollback_hash)
    },
    hashes: {
      baseline: $baseline_hash,
      updated: $updated_hash,
      rollback: $rollback_hash
    }
  }' > "$OUT_DIR/evidence.json"

pass "update rollback evidence written to artifacts/update-rollback/evidence.json"

# ── Test manual rollback via ods-update.sh rollback ──────────────────────────
# Set up a new environment representing a normal layered Compose installation.
# Prior to rollback, GPU_BACKEND=nvidia is active.
# Rollback restores a snapshot with GPU_BACKEND=cpu.
ROLLBACK_SRC="$TMP/ods-rollback-test"
mkdir -p "$ROLLBACK_SRC/lib" "$ROLLBACK_SRC/config" "$ROLLBACK_SRC/data/backups" "$ROLLBACK_SRC/bin"
cp "$ROOT_DIR/lib/rsync.sh" "$ROLLBACK_SRC/lib/rsync.sh"
cp "$ROOT_DIR/lib/safe-env.sh" "$ROLLBACK_SRC/lib/safe-env.sh"
cp "$ROOT_DIR/ods-update.sh" "$ROLLBACK_SRC/ods-update.sh"
chmod +x "$ROLLBACK_SRC/ods-update.sh"

# Mock wait_for_healthy requirements (must be able to find python-cmd.sh)
cp -r "$ROOT_DIR/lib" "$ROLLBACK_SRC/"

SNAP_TIMESTAMP="20260713-120000"
SNAP_DIR="$ROLLBACK_SRC/data/backups/pre-update-$SNAP_TIMESTAMP"
mkdir -p "$SNAP_DIR"

cat > "$SNAP_DIR/.version" <<'EOF'
{"version":"1.0.0"}
EOF
cat > "$SNAP_DIR/.env" <<'EOF'
ODS_MODE=local
GPU_BACKEND=cpu
GPU_COUNT=1
TIER=1
DASHBOARD_API_PORT=3002
OLLAMA_PORT=8080
EOF
cat > "$SNAP_DIR/docker-compose.base.yml" <<'EOF'
services:
  placeholder:
    image: busybox:1.36
EOF
cat > "$SNAP_DIR/docker-compose.cpu.yml" <<'EOF'
services:
  placeholder-cpu:
    image: busybox:1.36
EOF
cat > "$SNAP_DIR/snapshot.json" <<'EOF'
{"type":"pre-update","timestamp":"2026-07-13T12:00:00Z","version":"1.0.0","files_count":4,"install_dir":""}
EOF

# Pre-rollback state
cat > "$ROLLBACK_SRC/.env" <<'EOF'
ODS_MODE=local
GPU_BACKEND=nvidia
GPU_COUNT=1
TIER=1
DASHBOARD_API_PORT=3002
OLLAMA_PORT=8080
EOF
cat > "$ROLLBACK_SRC/docker-compose.base.yml" <<'EOF'
services:
  placeholder:
    image: busybox:1.36
EOF
cat > "$ROLLBACK_SRC/docker-compose.nvidia.yml" <<'EOF'
services:
  placeholder-nvidia:
    image: busybox:1.36
EOF
# Ensure no cached .compose-flags exists to force dynamic resolution
rm -f "$ROLLBACK_SRC/.compose-flags"

ROLLBACK_DOCKER_LOG="$TMP/docker-rollback-args.log"
export ROLLBACK_DOCKER_LOG
cat > "$ROLLBACK_SRC/bin/docker" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${ROLLBACK_DOCKER_LOG:?}"

if [[ "${1:-}" == "info" ]]; then
    exit 0
fi

if [[ "${1:-}" == "compose" && "${2:-}" == "version" ]]; then
    exit 0
fi

if [[ "${1:-}" == "compose" ]]; then
    shift
fi

args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
    if [[ "${args[$i]}" == "ps" ]]; then
        next="${args[$((i + 1))]:-}"
        if [[ "$next" == "--services" ]]; then
            printf '%s\n' placeholder
            exit 0
        fi
        if [[ "$next" == "--format" ]]; then
            printf '%s\n' '{"State":"running"}'
            exit 0
        fi
    fi
done

exit 0
SH
chmod +x "$ROLLBACK_SRC/bin/docker"

cat > "$ROLLBACK_SRC/bin/curl" <<SH
#!/usr/bin/env bash
exit 0
SH
chmod +x "$ROLLBACK_SRC/bin/curl"

# Run rollback command
PATH="$ROLLBACK_SRC/bin:$PATH" ODS_MODE=local bash "$ROLLBACK_SRC/ods-update.sh" rollback "$SNAP_TIMESTAMP" > "$TMP/rollback-run.log" 2>&1 || {
    cat "$TMP/rollback-run.log"
    fail "rollback execution failed"
}

# 1. Assert docker compose down receives the CURRENT stack flags (-f docker-compose.base.yml -f docker-compose.nvidia.yml)
if ! grep -q -- "-f docker-compose.base.yml -f docker-compose.nvidia.yml down" "$ROLLBACK_DOCKER_LOG" 2>/dev/null; then
    echo "=== DOCKER LOG ==="
    cat "$ROLLBACK_DOCKER_LOG" 2>/dev/null || echo "(empty)"
    fail "rollback down did not receive active pre-restore compose flags (-f docker-compose.base.yml -f docker-compose.nvidia.yml)"
fi

# 2. Assert docker compose up -d receives the RESTORED stack flags (-f docker-compose.base.yml -f docker-compose.cpu.yml)
if ! grep -q -- "-f docker-compose.base.yml -f docker-compose.cpu.yml up -d" "$ROLLBACK_DOCKER_LOG" 2>/dev/null; then
    echo "=== DOCKER LOG ==="
    cat "$ROLLBACK_DOCKER_LOG" 2>/dev/null || echo "(empty)"
    fail "rollback up did not receive restored post-restore compose flags (-f docker-compose.base.yml -f docker-compose.cpu.yml)"
fi

pass "rollback dynamically resolves distinct compose flags before and after restore"
