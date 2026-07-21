#!/usr/bin/env bash
# Path + shell hygiene audit for health-probe surfaces.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAIL=0

SURFACES=(
    "$ROOT/lib/service-registry.sh"
    "$ROOT/scripts/ods-doctor.sh"
    "$ROOT/scripts/health-check.sh"
    "$ROOT/scripts/showcase.sh"
    "$ROOT/scripts/validate.sh"
    "$ROOT/scripts/first-boot-demo.sh"
    "$ROOT/scripts/extension-runtime-check.sh"
    "$ROOT/ods-cli"
    "$ROOT/tests/test-health-probe-shadow-audit.sh"
    "$ROOT/tests/test-ods-cli-health-probe-contract.sh"
    "$ROOT/tests/test-service-manifest-health-contract-drift.sh"
)

echo "=== shell syntax (bash -n) ==="
for f in "${SURFACES[@]}"; do
    if [[ ! -f "$f" ]]; then
        echo "MISSING: $f" >&2
        FAIL=1
        continue
    fi
    if bash -n "$f"; then
        echo "OK bash -n $(basename "$f")"
    else
        echo "FAIL bash -n $f" >&2
        FAIL=1
    fi
done

echo "=== set -euo pipefail presence ==="
for f in "${SURFACES[@]}"; do
    [[ -f "$f" ]] || continue
    # service-registry is a library sourced into set -e hosts; still check top files
    case "$(basename "$f")" in
        service-registry.sh) continue ;;
    esac
    # Allow Bash 4 re-exec guards before set -euo (first 40 lines).
    if head -n 40 "$f" | grep -Eq 'set -euo pipefail|set -eu'; then
        echo "OK pipefail $(basename "$f")"
    else
        echo "FAIL missing pipefail in first 40 lines: $(basename "$f")" >&2
        FAIL=1
    fi
done

echo "=== no host absolute path hardcodes in health surfaces ==="
if command -v rg >/dev/null 2>&1; then
    if rg -n '/Users/|/home/daniellynch' "${SURFACES[@]}" 2>/dev/null; then
        echo "FAIL: host absolute paths in health surfaces" >&2
        FAIL=1
    else
        echo "OK no host absolute paths in health surfaces"
    fi
else
    echo "SKIP path hardcode scan (rg missing)"
fi

echo "=== registry resolve-before-probe contract ==="
if rg -q 'sr_resolve_ports' "$ROOT/ods-cli" "$ROOT/scripts/validate.sh" "$ROOT/scripts/ods-doctor.sh" "$ROOT/scripts/showcase.sh" "$ROOT/scripts/first-boot-demo.sh"; then
    echo "OK sr_resolve_ports used on major surfaces"
else
    echo "FAIL sr_resolve_ports missing from major surfaces" >&2
    FAIL=1
fi

if (( FAIL )); then
    echo "HEALTH_PROBE_PATH_SHELL_AUDIT_FAIL" >&2
    exit 1
fi
echo "HEALTH_PROBE_PATH_SHELL_AUDIT_OK"
