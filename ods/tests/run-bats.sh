#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Auto-install BATS if not present
if [[ ! -d "$SCRIPT_DIR/bats/bats-core" ]]; then
    echo "Installing BATS test framework..."
    mkdir -p "$SCRIPT_DIR/bats"
    git clone --depth 1 --branch v1.11.1 https://github.com/bats-core/bats-core.git "$SCRIPT_DIR/bats/bats-core"
    git clone --depth 1 --branch v0.3.0 https://github.com/bats-core/bats-support.git "$SCRIPT_DIR/bats/bats-support"
    git clone --depth 1 --branch v2.2.0 https://github.com/bats-core/bats-assert.git "$SCRIPT_DIR/bats/bats-assert"
fi

"$SCRIPT_DIR/bats/bats-core/bin/bats" "$SCRIPT_DIR"/bats-tests/*.bats "$@"
