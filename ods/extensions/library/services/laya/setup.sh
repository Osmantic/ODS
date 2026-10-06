#!/usr/bin/env bash
set -euo pipefail
extension_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec python3 "$extension_dir/setup.py" "${1:?ODS install directory is required}"
