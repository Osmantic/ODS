#!/usr/bin/env bash
# Shared verified-download helper for bootstrap.sh.
#
# download_verified downloads an artifact from an ordered list of HTTPS candidates
# (the primary URL first, then immutable mirrors), removes failed or hash-mismatched
# downloads, verifies the same pinned SHA-256 after every successful transport, and
# fails only after every candidate is exhausted. Non-HTTPS candidates are rejected
# and skipped. Credentials are never supplied.
#
# Requires pixel_log/pixel_die from scripts/lib/common.sh.

# Attempt one candidate with a bounded number of transport retries.
# Returns 0 only when the downloaded bytes pass every pinned check.
pixel_download_candidate() {
  local label=$1 url=$2 expected=$3 maximum_bytes=$4 destination=$5 integrity=$6
  local attempt attempts=3 observed actual_bytes observed_integrity
  attempt=0
  while [[ "$attempt" -lt "$attempts" ]]; do
    attempt=$((attempt + 1))
    rm -f -- "$destination"
    if curl -fsSL --proto '=https' --tlsv1.2 --max-time 300 --max-filesize "$maximum_bytes" "$url" -o "$destination"; then
      actual_bytes=$(wc -c < "$destination")
      if [[ "$actual_bytes" -le "$maximum_bytes" ]]; then
        observed=$(sha256sum "$destination" | awk '{print $1}')
        if [[ "$observed" == "$expected" ]]; then
          if [[ -n "$integrity" ]]; then
            observed_integrity="sha512-$(openssl dgst -sha512 -binary "$destination" | openssl base64 -A)"
            [[ "$observed_integrity" == "$integrity" ]] || { rm -f -- "$destination"; return 1; }
          fi
          return 0
        fi
      fi
      rm -f -- "$destination"
    else
      rm -f -- "$destination"
    fi
  done
  return 1
}

# download_verified LABEL EXPECTED_SHA MAX_BYTES DESTINATION [INTEGRITY] URL [URL...]
download_verified() {
  local label=$1 expected=$2 maximum_bytes=$3 destination=$4 integrity=${5:-}
  shift 5
  local url succeeded=0
  [[ "$expected" =~ ^[0-9a-f]{64}$ ]] || pixel_die "$label has an invalid pinned SHA-256"
  if [[ -n "$integrity" ]]; then
    [[ "$integrity" =~ ^sha512-[A-Za-z0-9+/]+=*$ ]] || pixel_die "$label has invalid pinned npm integrity"
  fi
  [[ $# -gt 0 ]] || pixel_die "$label has no HTTPS download candidate"
  for url in "$@"; do
    [[ "$url" == https://* ]] || { pixel_log "$label candidate is not HTTPS; rejecting $url"; continue; }
    if pixel_download_candidate "$label" "$url" "$expected" "$maximum_bytes" "$destination" "$integrity"; then
      succeeded=1
      break
    fi
    pixel_log "$label candidate failed: $url"
  done
  [[ "$succeeded" == 1 ]] || pixel_die "$label download failed after all candidates"
}
