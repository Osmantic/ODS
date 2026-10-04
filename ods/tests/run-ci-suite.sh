#!/usr/bin/env bash
# Run every test listed in tests/ci-suite.txt and fail if any of them fails.
# Each test runs on its own with a time limit, so one hang cannot stall the rest.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="$ROOT_DIR/tests/ci-suite.txt"
LIMIT="${ODS_CI_SUITE_TEST_TIMEOUT:-300}"
cd "$ROOT_DIR"

failed=()
total=0
while IFS= read -r test; do
    [[ -z "$test" || "$test" == \#* ]] && continue
    total=$((total + 1))
    case "$test" in
        *.sh) command=(bash "$test") ;;
        *.mjs) command=(node --test "$test") ;;
        */test_*.py) command=(python3 -m pytest -q -p no:cacheprovider "$test") ;;
        *.py) command=(python3 "$test") ;;
        *) echo "[FAIL] unsupported test type: $test"; failed+=("$test"); continue ;;
    esac
    echo "::group::$test"
    if timeout "$LIMIT" "${command[@]}" < /dev/null; then
        echo "::endgroup::"
    else
        status=$?
        echo "::endgroup::"
        echo "[FAIL] $test (exit $status)"
        failed+=("$test")
    fi
done < "$MANIFEST"

if [[ ${#failed[@]} -gt 0 ]]; then
    echo "[FAIL] ${#failed[@]} of $total tests failed:"
    printf '  %s\n' "${failed[@]}"
    exit 1
fi
echo "[PASS] all $total tests in tests/ci-suite.txt"
