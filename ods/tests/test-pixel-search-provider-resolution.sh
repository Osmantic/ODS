#!/usr/bin/env bash
# Variables below are consumed by the sourced Pixel installer helper.
# shellcheck disable=SC2034
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../installers/lib/pixel-host-install.sh
source "$SCRIPT_DIR/installers/lib/pixel-host-install.sh"

INSTALL_USER="$(id -un)"
scratch="$(mktemp -d)"
trap 'rm -rf -- "$scratch"' EXIT
INSTALL_DIR="$scratch/install"
mkdir -p "$INSTALL_DIR/data/pixel"
answers="$INSTALL_DIR/data/pixel/onboarding.json"

expect_provider() {
    local label="$1" expected="$2" actual
    actual="$(ods_pixel_resolve_search_provider)"
    [[ "$actual" == "$expected" ]] || {
        printf 'FAIL: %s: expected %s, got %s\n' "$label" "$expected" "$actual" >&2
        exit 1
    }
}

unset PIXEL_WEB_SEARCH_PROVIDER PIXEL_SERVICE_USER
expect_provider fresh parallel-free

PIXEL_WEB_SEARCH_PROVIDER=searxng
expect_provider explicit-searxng searxng
unset PIXEL_WEB_SEARCH_PROVIDER

printf 'PIXEL_WEB_SEARCH_PROVIDER="searxng" # retained\n' > "$INSTALL_DIR/.env"
expect_provider retained-env searxng
PIXEL_WEB_SEARCH_PROVIDER=parallel-free
expect_provider explicit-over-retained parallel-free
unset PIXEL_WEB_SEARCH_PROVIDER

rm -f -- "$INSTALL_DIR/.env"
printf '{"webSearchProvider":"searxng"}\n' > "$answers"
chmod 0600 "$answers"
expect_provider legacy-private-answers searxng

printf 'PIXEL_WEB_SEARCH_PROVIDER=parallel-free\n' > "$INSTALL_DIR/.env"
expect_provider explicit-env-over-legacy parallel-free
rm -f -- "$INSTALL_DIR/.env"

chmod 0644 "$answers"
if ods_pixel_resolve_search_provider > "$scratch/unsafe.stdout" 2> "$scratch/unsafe.stderr"; then
    echo 'FAIL: unsafe onboarding answers were accepted' >&2
    exit 1
fi
[[ ! -s "$scratch/unsafe.stdout" ]] || {
    echo 'FAIL: unsafe onboarding answers produced a provider' >&2
    exit 1
}

python3 - "$SCRIPT_DIR/config/extensions-catalog.json" <<'PY'
import json
import sys

catalog = json.load(open(sys.argv[1], encoding="utf-8"))
pixel = next(entry for entry in catalog["extensions"] if entry["id"] == "pixel-agent")
assert "searxng" not in pixel["depends_on"]
for feature in pixel["features"]:
    assert "searxng" not in feature.get("enabled_services_all", [])
    assert "searxng" not in feature.get("requirements", {}).get("services", [])
PY

echo 'PASS: Pixel provider resolution preserves explicit, retained, and owner-private choices'
