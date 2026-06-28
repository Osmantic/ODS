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
BACKUP_ID="$(find "$SRC/.backups" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort | tail -n 1)"
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
