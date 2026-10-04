#!/usr/bin/env bash
# Validate every extension library recipe the way ODS composes it: on top of the
# core stack (docker-compose.base.yml), with each GPU overlay, and with
# placeholder values for the ${VAR:?...} secrets the Dashboard generates when it
# installs a recipe. `docker compose config` checks structure, references and
# interpolation; it does not pull or run anything.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)/ods"
library=extensions/library/services

# Install-time secrets and settings that a recipe requires but CI cannot know.
while IFS= read -r name; do
    export "$name=ci-placeholder"
done < <(grep -rhoE '\$\{[A-Za-z_][A-Za-z0-9_]*:\?' "$library" docker-compose.base.yml \
             | sed -E 's/^\$\{([A-Za-z0-9_]+):\?$/\1/' | sort -u)

total=0
failed=()
for dir in "$library"/*/; do
    recipe="${dir}compose.yaml"
    [[ -f "$recipe" ]] || continue
    for overlay in "" "${dir}compose.nvidia.yaml" "${dir}compose.amd.yaml"; do
        [[ -z "$overlay" || -f "$overlay" ]] || continue
        files=(-f docker-compose.base.yml -f "$recipe")
        [[ -n "$overlay" ]] && files+=(-f "$overlay")
        label="$(basename "$dir")${overlay:+ + $(basename "$overlay")}"
        total=$((total + 1))
        if ! output="$(docker compose "${files[@]}" config --quiet 2>&1)"; then
            failed+=("$label")
            echo "::error::$label: $output"
        fi
    done
done

echo "Validated $total recipe configurations; ${#failed[@]} failed."
if [[ ${#failed[@]} -gt 0 ]]; then
    printf '  %s\n' "${failed[@]}"
    exit 1
fi
