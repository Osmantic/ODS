#!/usr/bin/env bash
# Public CLI coverage for legacy model inventory JSON.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT
mkdir -p "$WORKDIR/models/alpha" "$WORKDIR/models/beta"
printf '{}\n' > "$WORKDIR/models/alpha/config.json"
printf '{}\n' > "$WORKDIR/models/beta/config.json"
printf '{"current":"beta","previous":"alpha"}\n' > "$WORKDIR/model-state.json"

REPORT="$WORKDIR/report.json"
ODS_DIR="$WORKDIR" MODELS_DIR="$WORKDIR/models" \
    bash "$ROOT_DIR/scripts/upgrade-model.sh" --list --json > "$REPORT"

jq -e '
    .current == "beta" and
    (.models | length) == 2 and
    (.models | map(.name)) == ["alpha", "beta"] and
    (.models | map(select(.active) | .name)) == ["beta"] and
    (.models | all(.size | type == "string" and length > 0))
' "$REPORT" >/dev/null

echo "[PASS] model inventory JSON identifies every model and the active selection"
