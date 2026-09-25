#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
echo "scripts/smoke-test.sh is retained for compatibility; running ./pixel verify." >&2
exec bash "$ROOT/scripts/verify.sh" "$@"
