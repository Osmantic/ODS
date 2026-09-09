#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$ROOT_DIR/installers/phases/11-services.sh"

fail() { echo "[FAIL] $*" >&2; exit 1; }
function_block() {
    local function_name="$1"
    awk -v signature="^${function_name}[(][)]" '
        $0 ~ signature { in_block=1 }
        in_block { print }
        in_block && /^}/ { exit }
    ' "$TARGET"
}

eval "$(function_block _phase11_migrate_hermes_persisted_config)"
tmp="$(mktemp -d "${TMPDIR:-/tmp}/ods-linux-hermes-migration.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/install/scripts" "$tmp/install/data/hermes"
cp "$ROOT_DIR/scripts/patch-hermes-config.py" "$tmp/install/scripts/patch-hermes-config.py"
log_file="$tmp/install.log"
: > "$log_file"

persisted="$tmp/install/data/hermes/config.yaml"
cat > "$persisted" <<'YAML'
model:
  default: operator-model
  max_tokens: 1024 # ODS legacy
agent:
  disabled_toolsets:
    - terminal
    - browser
  # keep the operator note
  mode: autonomous
terminal:
  backend: local
  timeout: 30
YAML
chmod 640 "$persisted"
_BOOTSTRAP_ACTIVE=false
_phase11_migrate_hermes_persisted_config \
    "$(command -v python3)" "$tmp/install/scripts/patch-hermes-config.py" \
    "$persisted" "$log_file" \
    || fail "no-bootstrap Linux migration failed"
grep -Fq "  default: operator-model" "$persisted" || fail "operator model was lost"
grep -Fq "  # keep the operator note" "$persisted" || fail "operator comment was lost"
grep -Fq "  mode: autonomous" "$persisted" || fail "operator agent sibling was lost"
grep -Fq "  backend: local" "$persisted" || fail "terminal backend was lost"
if grep -Eq 'max_tokens: 1024|disabled_toolsets:|timeout: 30' "$persisted"; then
    fail "exact ODS legacy reductions survived the no-bootstrap Linux migration"
fi
[[ "$(stat -c '%a' "$persisted")" == "640" ]] || fail "atomic migration changed file mode"

divergent="$tmp/install/data/hermes/divergent.yaml"
cat > "$divergent" <<'YAML'
model:
  max_tokens: 2048
agent:
  disabled_toolsets:
    - terminal
    - browser
    - skills
terminal:
  timeout: 45
YAML
cp "$divergent" "$divergent.expected"
_phase11_migrate_hermes_persisted_config \
    "$(command -v python3)" "$tmp/install/scripts/patch-hermes-config.py" \
    "$divergent" "$log_file" \
    || fail "divergent-value migration invocation failed"
cmp -s "$divergent.expected" "$divergent" \
    || fail "divergent operator reductions were changed"

ln -s "$divergent" "$tmp/install/data/hermes/config-link.yaml"
if _phase11_migrate_hermes_persisted_config \
    "$(command -v python3)" "$tmp/install/scripts/patch-hermes-config.py" \
    "$tmp/install/data/hermes/config-link.yaml" "$log_file"; then
    fail "persisted-config migration followed a symlink"
fi

_phase11_migrate_hermes_persisted_config \
    "$(command -v python3)" "$tmp/install/scripts/patch-hermes-config.py" \
    "$tmp/install/data/hermes/not-created.yaml" "$log_file" \
    || fail "fresh install without persisted config should be a no-op"

echo "[PASS] Linux migrates exact persisted Hermes reductions without bootstrap or operator drift"
