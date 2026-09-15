#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="$ROOT_DIR/ods/ods-restore.sh"

grep -q 'local staging_dir="\$ODS_DIR/.config.restore.\$\$"' "$SCRIPT" \
    || { echo "FAIL: config restore is not staged before activation" >&2; exit 1; }
grep -q 'if ! cp -r "\$backup_dir/config" "\$staging_dir"' "$SCRIPT" \
    || { echo "FAIL: staging copy failure is not handled" >&2; exit 1; }
grep -q 'if ! restore_config "\$backup_dir"' "$SCRIPT" \
    || { echo "FAIL: configuration restore failures are ignored" >&2; exit 1; }
grep -q 'log_error "Configuration restore failed; restore was not completed."' "$SCRIPT" \
    || { echo "FAIL: configuration failure does not stop the restore" >&2; exit 1; }

echo "PASS: restore stages config and propagates replacement failures"
