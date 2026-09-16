#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

files=(
  "$ROOT/config/searxng/settings.yml"
  "$ROOT/installers/phases/06-directories.sh"
  "$ROOT/installers/macos/lib/env-generator.sh"
  "$ROOT/installers/windows/lib/env-generator.ps1"
)

for file in "${files[@]}"; do
  grep -A3 -F -- "- name: bing" "$file" | grep -Fq "disabled: true" || {
    echo "Unqualified Bing engine must be disabled in factory SearXNG configuration: $file" >&2
    exit 1
  }
  grep -A1 -F -- "- name: brave" "$file" | grep -Fq "disabled: false" || {
    echo "Brave must remain available in factory SearXNG configuration: $file" >&2
    exit 1
  }
done

echo "SearXNG config contract checks passed"
