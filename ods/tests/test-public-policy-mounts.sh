#!/usr/bin/env bash
# CI contract: strict-umask source staging still feeds the production UID.
set -euo pipefail

image="${1:?usage: test-public-policy-mounts.sh IMAGE SERVICE}"
service="${2:?usage: test-public-policy-mounts.sh IMAGE SERVICE}"
case "$service" in
    ape|remote-provider-egress) ;;
    *) printf 'Unsupported service: %s\n' "$service" >&2; exit 2 ;;
esac

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
scratch="$(mktemp -d)"
trap 'rm -rf -- "$scratch"' EXIT
(
    umask 077
    mkdir -p "$scratch/config/ape"
    cp "$repo_root/ods/config/ape/policy.yaml" "$scratch/config/ape/policy.yaml"
    cp "$repo_root/ods/config/remote-provider-egress-policy.json" \
        "$scratch/config/remote-provider-egress-policy.json"
    printf 'private\n' > "$scratch/config/ape/private-sibling"
)
test "$(stat -c %a "$scratch/config/ape")" = 700
test "$(stat -c %a "$scratch/config/ape/policy.yaml")" = 600
test "$(stat -c %a "$scratch/config/remote-provider-egress-policy.json")" = 600

bash "$repo_root/ods/scripts/prepare-public-policy-mounts.sh" "$scratch"
test "$(stat -c %a "$scratch/config/ape")" = 711
test "$(stat -c %a "$scratch/config/ape/policy.yaml")" = 644
test "$(stat -c %a "$scratch/config/remote-provider-egress-policy.json")" = 644
test "$(stat -c %a "$scratch/config/ape/private-sibling")" = 600

if [[ "$service" == ape ]]; then
    docker run --rm --network none --read-only --cap-drop ALL \
        --security-opt no-new-privileges \
        --mount "type=bind,src=$scratch/config/ape,dst=/config,readonly" \
        --entrypoint python "$image" -c \
        'import os; assert os.geteuid() == 100; assert "version: 1" in open("/config/policy.yaml").read(); assert not os.access("/config/private-sibling", os.R_OK)'
else
    docker run --rm --network none --read-only --cap-drop ALL \
        --security-opt no-new-privileges \
        --mount "type=bind,src=$scratch/config/remote-provider-egress-policy.json,dst=/config/remote-provider-egress-policy.json,readonly" \
        --entrypoint python "$image" -c \
        'import json,os; assert os.geteuid() == 10778; assert json.load(open("/config/remote-provider-egress-policy.json"))["version"] == 1'
fi

# Re-render/re-run repairs the policy modes without broadening private siblings.
chmod 0700 "$scratch/config/ape"
chmod 0600 "$scratch/config/ape/policy.yaml" \
    "$scratch/config/remote-provider-egress-policy.json"
bash "$repo_root/ods/scripts/prepare-public-policy-mounts.sh" "$scratch"
test "$(stat -c %a "$scratch/config/ape/private-sibling")" = 600

# A substituted policy path must not turn chmod into a write through a symlink.
rm "$scratch/config/ape/policy.yaml"
ln -s private-sibling "$scratch/config/ape/policy.yaml"
if bash "$repo_root/ods/scripts/prepare-public-policy-mounts.sh" "$scratch" 2>/dev/null; then
    printf 'Symlinked policy unexpectedly accepted\n' >&2
    exit 1
fi
test "$(stat -c %a "$scratch/config/ape/private-sibling")" = 600
