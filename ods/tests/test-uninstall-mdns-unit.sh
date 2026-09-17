#!/usr/bin/env bash
set -euo pipefail
python3 "$(dirname "$0")/test_system_uninstall.py" -v
