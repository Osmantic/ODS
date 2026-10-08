#!/usr/bin/env bash
# docker-compose.tier0.yml caps memory for machines below 8GB RAM.
# installers/lib/compose-select.sh adds it while the installer runs, but
# phases 03 and 11 then replace COMPOSE_FLAGS with resolve-compose-stack.sh
# output and cache that to .compose-flags. The resolver never applied the
# overlay, so tier 0 installs ran on the base limits (llama-server 6G instead
# of 4G, dashboards 2G instead of 512M), and ods-cli, the host agent and
# ods-update.sh dropped it again on every later resolution.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESOLVER="$ROOT_DIR/scripts/resolve-compose-stack.sh"

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); printf '[PASS] %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf '[FAIL] %s\n' "$1" >&2; }

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

# A minimal install tree: core compose files plus the overlays under test.
FIXTURE="$TMP_DIR/install"
mkdir -p "$FIXTURE/extensions/services"
printf 'services:\n  llama-server:\n    image: example/llama\n  dashboard:\n    image: example/dashboard\n' \
    > "$FIXTURE/docker-compose.base.yml"
printf 'services:\n  llama-server:\n    image: example/llama-cpu\n' > "$FIXTURE/docker-compose.cpu.yml"
cp "$ROOT_DIR/docker-compose.tier0.yml" "$FIXTURE/docker-compose.tier0.yml"

resolve() {  # resolve <tier>
    bash "$RESOLVER" --script-dir "$FIXTURE" --tier "$1" --gpu-backend cpu --gpu-count 1 --ods-mode local
}

flags="$(resolve 0)"
if grep -q -- '-f docker-compose.tier0.yml' <<< "$flags"; then
    pass "tier 0 resolves with the tier 0 memory overlay"
else
    printf '%s\n' "$flags"
    fail "tier 0 resolved without docker-compose.tier0.yml"
fi

flags="$(resolve T0)"
if grep -q -- '-f docker-compose.tier0.yml' <<< "$flags"; then
    pass "the T0 spelling resolves with the overlay too"
else
    printf '%s\n' "$flags"
    fail "tier T0 resolved without docker-compose.tier0.yml"
fi

flags="$(resolve 1)"
if ! grep -q -- 'docker-compose.tier0.yml' <<< "$flags"; then
    pass "tier 1 does not pick up the tier 0 overlay"
else
    printf '%s\n' "$flags"
    fail "tier 1 resolved with docker-compose.tier0.yml"
fi

# Operator overrides must stay the final authority.
printf 'services:\n  operator-extra:\n    image: example/extra\n' > "$FIXTURE/docker-compose.override.yml"
flags="$(resolve 0)"
tier0_pos=$(awk '{for (i = 1; i <= NF; i++) if ($i == "docker-compose.tier0.yml") print i}' <<< "$flags")
override_pos=$(awk '{for (i = 1; i <= NF; i++) if ($i == "docker-compose.override.yml") print i}' <<< "$flags")
if [[ -n "$tier0_pos" && -n "$override_pos" ]] && (( tier0_pos < override_pos )); then
    pass "the overlay is applied before docker-compose.override.yml"
else
    printf '%s\n' "$flags"
    fail "expected tier0 before the operator override (tier0=$tier0_pos override=$override_pos)"
fi
rm -f "$FIXTURE/docker-compose.override.yml"

# An install tree without the overlay file must still resolve.
rm -f "$FIXTURE/docker-compose.tier0.yml"
if flags="$(resolve 0)" && grep -q -- '-f docker-compose.base.yml' <<< "$flags"; then
    pass "a tree without the overlay file still resolves"
else
    fail "resolution failed when docker-compose.tier0.yml is absent"
fi

printf 'Results: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
