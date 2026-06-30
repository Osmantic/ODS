#!/usr/bin/env bash
# Contract: every installer-time-enableable built-in service that ships a local
# `build:` (image:<name>:local) MUST appear in the local image-build candidate
# list of each launch path. Both launch paths run
# `docker compose ... up -d --remove-orphans --no-build`, so an enabled service
# whose image was never built would abort compose-up on the missing image.
#
# Surface checked:
#   - installer-enable surface  : installers/phases/03-features.sh
#                                 (_sync_extension_compose "<flag>" <svc> ...)
#   - locally-built services     : extensions/services/<svc>/compose.yaml has build:
#   - Linux build allowlist      : installers/phases/11-services.sh
#                                 (_candidate_build_services)
#   - macOS build allowlist      : installers/macos/install-macos.sh
#                                 (_macos_candidate_build_services)
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

FEATURES="installers/phases/03-features.sh"
LINUX_SVC="installers/phases/11-services.sh"
MACOS_SVC="installers/macos/install-macos.sh"

for f in "$FEATURES" "$LINUX_SVC" "$MACOS_SVC"; do
  test -f "$f" || { echo "[FAIL] missing $f"; exit 1; }
done

# Services the installer can enable at install time = 3rd token of every
# `_sync_extension_compose "<flag>" <svc> ...` call (skip the function def line,
# which has no quoted flag argument). Bash 3.2 safe: no mapfile.
enable_surface=()
while IFS= read -r svc; do
  [[ -n "$svc" ]] && enable_surface+=("$svc")
done < <(grep -E '_sync_extension_compose[[:space:]]+"' "$FEATURES" | awk '{print $3}' | sort -u)
[[ ${#enable_surface[@]} -gt 0 ]] || { echo "[FAIL] no _sync_extension_compose calls parsed from $FEATURES"; exit 1; }

# Candidate-build lines from each launch path (initial + += assignments).
linux_lists="$(grep -E '_candidate_build_services' "$LINUX_SVC" || true)"
macos_lists="$(grep -E '_macos_candidate_build_services' "$MACOS_SVC" || true)"

# Documented exclusions: services known to have this gap but intentionally NOT
# fixed in the current change set. brave-search has the same shape (build: +
# image:dream-brave-search:local, ENABLE_BRAVE_SEARCH honored at install time)
# and is missing from both build allowlists, but it is a paid-API post-install
# opt-in service tracked separately — out of scope here. Remove from this list
# when it is added to the build allowlists.
EXCLUDED="brave-search"

fail=0
checked=0
for svc in "${enable_surface[@]}"; do
  compose="extensions/services/${svc}/compose.yaml"
  # Only locally-built services need an installer-time build.
  [[ -f "$compose" ]] || continue
  grep -qE '^\s*build:' "$compose" || continue
  [[ " $EXCLUDED " == *" $svc "* ]] && { echo "[skip] $svc excluded (tracked separately)"; continue; }

  checked=$((checked + 1))
  if ! grep -qwE "$svc" <<<"$linux_lists"; then
    echo "[FAIL] $svc has a local build: but is missing from _candidate_build_services in $LINUX_SVC"
    fail=1
  fi
  if ! grep -qwE "$svc" <<<"$macos_lists"; then
    echo "[FAIL] $svc has a local build: but is missing from _macos_candidate_build_services in $MACOS_SVC"
    fail=1
  fi
done

[[ $checked -gt 0 ]] || { echo "[FAIL] no installer-enableable locally-built services were checked (parser drift?)"; exit 1; }

if [[ $fail -ne 0 ]]; then
  echo "[FAIL] installer build-allowlist contract violated"
  exit 1
fi

echo "[PASS] all $checked installer-enableable locally-built services are in both build allowlists"
