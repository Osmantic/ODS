#!/usr/bin/env bash
# Every failure surface points people at the same ODS community Discord, and
# no other Discord invite ships. The canonical invite is ODS_HELP_DISCORD_URL in
# installers/lib/constants.sh.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

url="$(sed -n 's/^ODS_HELP_DISCORD_URL="\(.*\)"$/\1/p' installers/lib/constants.sh)"
[[ "$url" =~ ^https://discord\.gg/[A-Za-z0-9]+$ ]] || fail "ODS_HELP_DISCORD_URL is missing or malformed: '$url'"
pass "canonical help invite: $url"

# Surfaces that must show the help link when something fails.
surfaces=(
    install-core.sh
    installers/macos/lib/constants.sh
    installers/macos/install-macos.sh
    scripts/ods-doctor.sh
    ods-cli
    installers/windows-portal.ps1
    installers/windows/lib/ui.ps1
    extensions/services/dashboard/src/lib/support.js
)
for file in "${surfaces[@]}"; do
    grep -qF "$url" "$file" || fail "$file does not point at the ODS help Discord ($url)"
done
pass "installer, doctor, Windows setup and dashboard all link the help Discord"

# The macOS installer prints the link on any failed exit.
grep -q '^trap _macos_help_on_failure EXIT$' installers/macos/install-macos.sh \
    || fail "install-macos.sh does not print the help link when it fails"
pass "the macOS installer prints the help link on a failed exit"

# The native Windows installer stops through the helper that prints the link.
if grep -nE '^[[:space:]]*exit 1[[:space:]]*$|\{ exit 1 \}' installers/windows/install-windows.ps1; then
    fail "install-windows.ps1 exits without the help link; use Exit-ODSInstallFailure"
fi
pass "the native Windows installer exits through Exit-ODSInstallFailure"

# No other invite ships (an older guide pointed at an unrelated server).
others="$(grep -rIoE 'discord\.(gg|com/invite)/[A-Za-z0-9]+' \
    --exclude-dir=node_modules --exclude-dir=vendor --exclude-dir=.git \
    --exclude-dir=dist . | grep -vF "${url#https://}" || true)"
[[ -z "$others" ]] || fail "other Discord invites ship: $others"
pass "no other Discord invite ships"
