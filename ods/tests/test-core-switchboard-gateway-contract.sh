#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE="$ROOT_DIR/installers/phases/03-features.sh"

bash -n "$PHASE"
grep -q 'ODS_MODEL_SWITCHBOARD:-enabled' "$PHASE"
grep -q '_sync_extension_compose "\$_litellm_required" litellm' "$PHASE"

echo "[PASS] enabled model switchboard keeps LiteLLM in Core Only stacks"
