#!/usr/bin/env bash
# Pure budget regression. No service, model, config, or repository mutations.
set -euo pipefail
TEST_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
. "$TEST_DIR/../installers/lib/pixel-host-install.sh"
checks=0
for pair in 4096:1024 8192:2048 16384:4096 32768:8192 65536:8192 131072:8192 262144:8192 10000000:8192 065536:8192; do
    context="${pair%:*}"
    expected="${pair#*:}"
    [[ "$(_ods_pixel_default_output_tokens "$context")" == "$expected" ]]
    checks=$((checks + 1))
done
for context in '' 2048 -4096 4096.0 invalid 10000001 999999999999999999999999 '4096+1'; do
    if _ods_pixel_default_output_tokens "$context" >/dev/null 2>&1; then
        printf 'unexpected accepted context: %s\n' "$context" >&2
        exit 1
    fi
    checks=$((checks + 1))
done
# Both production entry points must call the one shared policy, not keep a
# second hard-coded budget that diverges on upgrade.
grep -Fq 'max_tokens="$(_ods_pixel_default_output_tokens "$context")"' "$TEST_DIR/../installers/lib/pixel-host-install.sh"
grep -Fq 'target_max_tokens="$(_ods_pixel_default_output_tokens "$target_context")"' "$TEST_DIR/../scripts/bootstrap-upgrade.sh"
checks=$((checks + 2))
printf 'PASS: %s output-budget checks\n' "$checks"
