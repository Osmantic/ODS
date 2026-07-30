#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BATS_BIN="$SCRIPT_DIR/bats/bats-core/bin/bats"

if [[ ! -x "$BATS_BIN" || ! -f "$SCRIPT_DIR/bats/bats-support/load.bash" || ! -f "$SCRIPT_DIR/bats/bats-assert/load.bash" ]]; then
	echo "Bats submodules are missing; run: git submodule update --init --recursive" >&2
	exit 2
fi

exec "$BATS_BIN" "$SCRIPT_DIR"/bats-tests/*.bats "$@"
