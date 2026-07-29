#!/usr/bin/env bash
# Verifies the macOS tier map stays in sync with the canonical tier map.
# installers/macos/lib/tier-map.sh declares "keep values byte-identical" with
# installers/lib/tier-map.sh, but nothing enforced it — a stale GGUF_SHA256
# in the macOS copy breaks checksum verification on the tier-map fallback path.
# For every GGUF_FILE both maps define, the SHA256 and URL must match.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CANONICAL="$ROOT_DIR/installers/lib/tier-map.sh"
MACOS="$ROOT_DIR/installers/macos/lib/tier-map.sh"

fail() { echo "[FAIL] $*"; exit 1; }
pass() { echo "[PASS] $*"; }

[[ -f "$CANONICAL" ]] || fail "canonical tier map not found"
[[ -f "$MACOS" ]] || fail "macOS tier map not found"

# extract_pairs <file> <key> — emit "GGUF_FILE<TAB>value" for each tier block
extract_pairs() {
    local file="$1" key="$2"
    awk -v key="$key" '
        /GGUF_FILE="/ {
            gsub(/.*GGUF_FILE="/, ""); gsub(/".*/, "")
            current_file = $0
            next
        }
        $0 ~ key"=\"" {
            gsub(".*" key "=\"", ""); gsub(/".*/, "")
            if (current_file != "") {
                print current_file "\t" $0
                current_file = ""
            }
        }
    ' "$file"
}

checked=0
mismatches=0
for key in GGUF_SHA256 GGUF_URL; do
    while IFS=$'\t' read -r gguf value; do
        canonical_value=$(extract_pairs "$CANONICAL" "$key" | awk -F'\t' -v f="$gguf" '$1==f {print $2; exit}')
        [[ -n "$canonical_value" ]] || continue  # macOS-only entry: nothing to compare
        checked=$(( checked + 1 ))
        if [[ "$value" != "$canonical_value" ]]; then
            echo "[MISMATCH] $gguf $key"
            echo "  macOS:     $value"
            echo "  canonical: $canonical_value"
            mismatches=$(( mismatches + 1 ))
        fi
    done < <(extract_pairs "$MACOS" "$key")
done

[[ $checked -gt 0 ]] || fail "no comparable GGUF entries found (extraction broken?)"
[[ $mismatches -eq 0 ]] || fail "$mismatches value(s) differ between macOS and canonical tier maps"
pass "macOS tier map matches canonical for $checked GGUF field(s)"
