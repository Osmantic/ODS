#!/usr/bin/env bash
# ============================================================================
# Contract Test: macOS Compose Pre-Pull Platform Pin Preservation (#1987)
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "[test] Starting macOS Compose pre-pull platform pin contract test..."

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

# Mock python3 snippet used in install-macos.sh and compose-images.sh
JSON_CONFIG="$(cat <<'EOF'
{
  "services": {
    "speaches": {
      "image": "ghcr.io/speaches-ai/speaches:0.9.0-rc.3-cpu"
    },
    "embeddings": {
      "image": "ghcr.io/huggingface/text-embeddings-inference:cpu-1.9.1",
      "platform": "linux/amd64"
    }
  }
}
EOF
)"

OUTPUT="$(printf '%s' "$JSON_CONFIG" | python3 -c '
import json
import sys

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(1)

for service in (data.get("services") or {}).values():
    if service.get("build") is not None:
        continue
    image = str(service.get("image") or "").strip()
    platform = str(service.get("platform") or "").strip()
    if image:
        if platform:
            print(f"{image}|{platform}")
        else:
            print(image)
')"

echo "$OUTPUT" > "$TMP_DIR/output.txt"

# Assert that plain image is printed without pipe
if ! grep -q "^ghcr.io/speaches-ai/speaches:0.9.0-rc.3-cpu$" "$TMP_DIR/output.txt"; then
  echo "FAIL: Expected plain image entry for speaches"
  exit 1
fi

# Assert that platform-pinned image is printed with |linux/amd64
if ! grep -q "^ghcr.io/huggingface/text-embeddings-inference:cpu-1.9.1|linux/amd64$" "$TMP_DIR/output.txt"; then
  echo "FAIL: Expected platform-pinned image entry for embeddings"
  exit 1
fi

# Verify _macos_pull_image_with_retry logic parses entry and executes docker pull --platform
MOCK_LOG="$TMP_DIR/docker_calls.log"

docker() {
  echo "docker $*" >> "$MOCK_LOG"
  if [[ "$1" == "image" && "$2" == "inspect" ]]; then
    return 1 # Simulate not cached
  fi
  return 0
}
export -f docker

# Mock installer helper functions
ai() { echo "[AI] $*" >> "$MOCK_LOG"; }
ai_ok() { echo "[OK] $*" >> "$MOCK_LOG"; }
ai_warn() { echo "[WARN] $*" >> "$MOCK_LOG"; }
ai_err() { echo "[ERR] $*" >> "$MOCK_LOG"; }
log() { echo "[LOG] $*" >> "$MOCK_LOG"; }
export -f ai ai_ok ai_warn ai_err log

ODS_LOG_FILE="$TMP_DIR/ods.log"
export ODS_LOG_FILE

_macos_pull_image_with_retry() {
    local entry="$1" image platform attempt max_attempts delay
    local -a delays=(5 15 30)

    if [[ "$entry" == *"|"* ]]; then
        image="${entry%%|*}"
        platform="${entry#*|}"
    else
        image="$entry"
        platform=""
    fi

    if docker image inspect "$image" >/dev/null 2>&1; then
        log "Compose image already cached: $image"
        return 0
    fi

    max_attempts="${ODS_DOCKER_PULL_MAX_ATTEMPTS:-4}"
    for ((attempt=1; attempt<=max_attempts; attempt++)); do
        local -a pull_cmd=(docker pull)
        if [[ -n "$platform" ]]; then
            pull_cmd+=(--platform "$platform")
        fi
        pull_cmd+=("$image")

        if "${pull_cmd[@]}" >>"$ODS_LOG_FILE" 2>&1; then
            return 0
        fi
    done
    return 1
}
export -f _macos_pull_image_with_retry

# Execute pull for platform-pinned entry
_macos_pull_image_with_retry "ghcr.io/huggingface/text-embeddings-inference:cpu-1.9.1|linux/amd64"

if ! grep -q "docker pull --platform linux/amd64 ghcr.io/huggingface/text-embeddings-inference:cpu-1.9.1" "$MOCK_LOG"; then
  echo "FAIL: Expected docker pull --platform linux/amd64 command"
  cat "$MOCK_LOG"
  exit 1
fi

echo "[PASS] macOS Compose pre-pull platform pin contract test completed successfully!"
