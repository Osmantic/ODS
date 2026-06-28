#!/bin/bash
# test-install.sh — Run a full install with the smallest model for fast testing
#
# Uses Tier 0 (Qwen 3.5 2B, ~1.5GB) and non-interactive mode.
# All other installer behavior is identical to a normal install.
#
# Usage:
#   ./test-install.sh                    # Minimal install, tiny model
#   ./test-install.sh --voice            # With voice services
#   ./test-install.sh --all              # All services, tiny model
#   ./test-install.sh --dry-run          # Preview without changes

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec "$SCRIPT_DIR/install.sh" --tier 0 --non-interactive "$@"
