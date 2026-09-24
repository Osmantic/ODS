#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SHEPHERD="$ROOT_DIR/memory-shepherd/memory-shepherd.sh"
TEST_ROOT="$(mktemp -d -t memory-shepherd-paths-XXXXXX)"
trap 'rm -rf "$TEST_ROOT"' EXIT

home_dir="$TEST_ROOT/home"
baseline_dir="$home_dir/ods/memory-shepherd/baselines"
archive_dir="$home_dir/ods/data/memory-archives/demo"
memory_dir="$home_dir/ods/config/openclaw/workspace"
mkdir -p "$baseline_dir" "$memory_dir"

{
    printf '# Baseline\n\n'
    for _ in {1..80}; do
        printf 'Operator-controlled baseline content.\n'
    done
} > "$baseline_dir/demo.md"
printf '# Current memory\n\n---\n\n## Scratch Notes\nkeep this note\n' \
    > "$memory_dir/MEMORY.md"

config="$TEST_ROOT/memory-shepherd.conf"
printf '%s\n' \
    '[general]' \
    'baseline_dir=~/ods/memory-shepherd/baselines' \
    'archive_dir=~/ods/data/memory-archives' \
    'min_baseline_size=500' \
    '[demo]' \
    'memory_file=~/ods/config/openclaw/workspace/MEMORY.md' \
    'baseline=demo.md' \
    'archive_subdir=demo' \
    > "$config"

HOME="$home_dir" MEMORY_SHEPHERD_CONF="$config" bash "$SHEPHERD" demo \
    > "$TEST_ROOT/output"

cmp "$baseline_dir/demo.md" "$memory_dir/MEMORY.md"
archive_file="$(find "$archive_dir" -type f -name '*.md' -print -quit)"
[[ -n "$archive_file" ]]
grep -Fq 'keep this note' "$archive_file"

install_output="$(HOME="$home_dir" MEMORY_SHEPHERD_CONF="$config" \
    bash "$ROOT_DIR/memory-shepherd/install.sh" \
    --dry-run --prefix "$TEST_ROOT/units")"
[[ "$install_output" == *"Baselines:     $baseline_dir"* ]]
[[ "$install_output" == *"Archives:      ${archive_dir%/demo}"* ]]
[[ "$install_output" != *"memory-shepherd/~/ods"* ]]

if find "$ROOT_DIR/memory-shepherd" -maxdepth 1 -name '~' -print -quit | grep -q .; then
    echo "tilde path was resolved beneath the script directory" >&2
    exit 1
fi

echo "memory shepherd path tests passed"
