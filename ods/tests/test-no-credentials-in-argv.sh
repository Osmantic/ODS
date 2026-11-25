#!/usr/bin/env bash
# Shipped scripts must never put a credential header on a command line: any
# local user can read every process's arguments with ps or
# /proc/<pid>/cmdline. Pass it to curl through a header file
# (-H @<(printf 'Authorization: Bearer %s\n' "$key")) or on stdin (-H @-).
#
# Run from ods/: bash tests/test-no-credentials-in-argv.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# A credential header whose value a shell expands into the arguments, whether
# written inline or collected in an array first.
pattern='(-H|--header)[[:space:]]+"(Authorization|Proxy-Authorization|X-API-Key|X-Api-Key|x-api-key|api-key):[^"]*\$'

mapfile -t files < <(find . -type f \( -name '*.sh' -o -name ods-cli \) \
    -not -path './tests/*' -not -path './vendor/*' -not -path '*/node_modules/*' | sort)
[[ "${#files[@]}" -gt 0 ]] || { printf 'FAIL: no shipped scripts found under %s\n' "$ROOT_DIR" >&2; exit 1; }

hits=""
for file in "${files[@]}"; do
    # grep exits 1 for a file with no match, which is the expected case.
    matches="$(grep -nE -- "$pattern" "$file" | grep -vE '^[0-9]+:[[:space:]]*#' || true)"
    if [[ -n "$matches" ]]; then
        hits+="$(sed "s|^|${file#./}:|" <<<"$matches")"$'\n'
    fi
done

if [[ -n "$hits" ]]; then
    printf 'FAIL: credentials on a command line, readable by any local user:\n%s' "$hits" >&2
    exit 1
fi
printf 'PASS: no shipped script puts a credential header on a command line (%d files)\n' "${#files[@]}"
