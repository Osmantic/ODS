#!/usr/bin/env bash
# Make the two nonsecret policy bind mounts readable by their non-root services.
# Keep all other config files, including credentials, at their existing modes.
set -euo pipefail

install_dir="${1:?usage: prepare-public-policy-mounts.sh INSTALL_DIR}"
config_dir="$install_dir/config"
ape_dir="$config_dir/ape"
ape_policy="$ape_dir/policy.yaml"
egress_policy="$config_dir/remote-provider-egress-policy.json"

for dir in "$install_dir" "$config_dir" "$ape_dir"; do
    if [[ ! -d "$dir" || -L "$dir" ]]; then
        printf 'Unsafe public policy directory: %s\n' "$dir" >&2
        exit 1
    fi
done
for file in "$ape_policy" "$egress_policy"; do
    if [[ ! -f "$file" || -L "$file" ]]; then
        printf 'Missing or unsafe public policy file: %s\n' "$file" >&2
        exit 1
    fi
done

# APE bind-mounts its config directory. It needs traversal, while the policy
# file itself is public; other entries in that directory remain private.
chmod 0711 "$ape_dir"
chmod 0644 "$ape_policy" "$egress_policy"

if [[ "$(stat -c %a "$ape_dir")" != 711 \
    || "$(stat -c %a "$ape_policy")" != 644 \
    || "$(stat -c %a "$egress_policy")" != 644 ]]; then
    printf 'Public policy mount permissions were not applied\n' >&2
    exit 1
fi
