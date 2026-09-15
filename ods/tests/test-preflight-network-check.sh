#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="$ROOT_DIR/installers/phases/01-preflight.sh"

grep -q '_phase01_check_required_network()' "$SOURCE"
grep -q 'OFFLINE_MODE:-false' "$SOURCE"
grep -q -- '--connect-timeout 5 --max-time 10' "$SOURCE"
grep -q 'Could not reach \${target_name}' "$SOURCE"
grep -q 'GitHub|https://github.com' "$SOURCE"
grep -q 'Docker Hub|https://registry-1.docker.io/v2/' "$SOURCE"

echo '[PASS] Phase 01 network preflight is bounded and offline-aware'
