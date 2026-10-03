#!/usr/bin/env bash
# Variables below are consumed by functions imported from pixel-host-install.sh.
# shellcheck disable=SC2034
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_DIR="$root"
# Importing this library must not mutate the host.
source "$root/installers/lib/pixel-host-install.sh"
scratch="$(mktemp -d)"
trap 'rm -rf -- "$scratch"' EXIT
printf 'microsoft\n' > "$scratch/wsl-kernel"
printf 'linux\n' > "$scratch/linux-kernel"
classifier="$root/installers/lib/wsl_pixel_mount_plan.py"

ai_bad() { printf '%s\n' "$*" >&2; }
ods_sudo() {
    case "$MOCK_STATUS" in
        clear|ordinary)
            printf '{"status":"%s"}\n' "$MOCK_STATUS"
            ;;
        needs-owned-edge-stop)
            printf '{"status":"%s"}\n' "$MOCK_STATUS"
            return 3
            ;;
        refuse)
            printf '{"status":"refuse"}\n'
            return 1
            ;;
        malformed)
            printf 'not json\n'
            ;;
        *) return 1 ;;
    esac
}

ENABLE_PIXEL_RUNTIME=false MOCK_STATUS=refuse
ods_pixel_admit_wsl_mount_upgrade "$scratch/wsl-kernel" "$classifier"
ENABLE_PIXEL_RUNTIME=true
ods_pixel_admit_wsl_mount_upgrade "$scratch/linux-kernel" "$classifier"

MOCK_STATUS=clear
ods_pixel_admit_wsl_mount_upgrade "$scratch/wsl-kernel" "$classifier"
for MOCK_STATUS in ordinary needs-owned-edge-stop refuse malformed; do
    if ods_pixel_admit_wsl_mount_upgrade "$scratch/wsl-kernel" "$classifier" 2>"$scratch/error"; then
        echo "WSL pre-phase admission accepted $MOCK_STATUS" >&2
        exit 1
    fi
    grep -Eq 'mount admission|socket migration' "$scratch/error"
done

MOCK_STATUS=clear
: > "$scratch/empty-kernel"
mkdir "$scratch/not-a-file"
for kernel in "$scratch/missing-kernel" "$scratch/empty-kernel" "$scratch/not-a-file"; do
    if ods_pixel_admit_wsl_mount_upgrade "$kernel" "$classifier" 2>"$scratch/error"; then
        echo "WSL pre-phase admission accepted unavailable kernel identity" >&2
        exit 1
    fi
    grep -q 'kernel identity' "$scratch/error"
done
printf 'microsoft\n' > "$scratch/read-error-kernel"
# The imported guard invokes this test-only stub indirectly.
# shellcheck disable=SC2317
cat() {
    if [[ "${2:-}" == "$scratch/read-error-kernel" ]]; then return 2; fi
    command cat "$@"
}
if ods_pixel_admit_wsl_mount_upgrade "$scratch/read-error-kernel" "$classifier" 2>"$scratch/error"; then
    echo "WSL pre-phase admission accepted a kernel read error" >&2
    exit 1
fi
grep -q 'kernel identity' "$scratch/error"
unset -f cat

python3 - "$root/install-core.sh" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1]).read_text(encoding="utf-8")
lock = source.index('ods_model_lifecycle_lock_acquire "$INSTALL_DIR"')
admission = source.index('ods_pixel_admit_wsl_mount_upgrade || exit 1')
phase06 = source.index('source "$SCRIPT_DIR/installers/phases/06-directories.sh"')
assert lock < admission < phase06
PY

echo "Pixel WSL pre-Phase06 mount admission passed"
