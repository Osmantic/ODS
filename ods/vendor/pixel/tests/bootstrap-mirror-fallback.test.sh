#!/usr/bin/env bash
# Network-free test of the verified installer/mirror fallback download helper.
#
# Uses a fake `curl` on PATH backed by a deterministic response table to prove:
#   1. primary HTTP 429 (transport failure) then mirror success;
#   2. primary wrong SHA-256 then mirror success (mismatched bytes are never accepted);
#   3. all candidates fail -> overall failure, failed downloads are removed;
#   4. non-HTTPS mirrors are rejected and never attempted;
#   5. no-candidate input fails cleanly.
set -euo pipefail
SOURCE=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT

mkdir -p "$tmp/bin"
cat > "$tmp/bin/curl" <<'FAKE'
#!/usr/bin/env bash
url=""
dest=""
prev=""
for arg in "$@"; do
  if [[ "$prev" == "-o" ]]; then dest="$arg"; fi
  if [[ "$arg" == http* ]]; then url="$arg"; fi
  prev="$arg"
done
if [[ -z "$url" || -z "$dest" ]]; then echo "fake curl: missing url/dest" >&2; exit 2; fi
printf '%s\n' "$url" >> "$FAKE_CURL_LOG"
while IFS=$'\t' read -r conf_url status content; do
  if [[ "$conf_url" == "$url" ]]; then
    if [[ "$status" == "ok" ]]; then
      cat "$content" > "$dest"
      exit 0
    fi
    exit 22
  fi
done < "$FAKE_CURL_CONF"
echo "fake curl: no response configured for $url" >&2
exit 22
FAKE
chmod +x "$tmp/bin/curl"
export PATH="$tmp/bin:$PATH"
export FAKE_CURL_CONF="$tmp/responses.tsv"
export FAKE_CURL_LOG="$tmp/curl.log"
: > "$FAKE_CURL_LOG"

# shellcheck source=scripts/lib/common.sh
source "$SOURCE/scripts/lib/common.sh"
# shellcheck source=scripts/lib/bootstrap-download.sh
source "$SOURCE/scripts/lib/bootstrap-download.sh"
# Derive the pin URLs from the generated release environment so the test never
# duplicates an authored release pin (which the release contract rejects).
# shellcheck source=scripts/generated/release.env
set -a
source "$SOURCE/scripts/generated/release.env"
set +a
primary=$PIXEL_GENERATED_OPENCLAW_INSTALLER_URL
mirror=${PIXEL_GENERATED_OPENCLAW_INSTALLER_MIRRORS[0]}

good="$tmp/good.txt"
bad="$tmp/bad.txt"
printf 'correct installer bytes\n' > "$good"
printf 'tampered installer bytes\n' > "$bad"
expected=$(sha256sum "$good" | awk '{print $1}')

fail_expected() {
  local out=$1 err=$2
  shift 2
  if ( download_verified "$@" >"$out" 2>"$err" ); then
    echo "expected download failure but it succeeded" >&2
    exit 1
  fi
}

# 1. Primary transport failure (429 -> curl 22) then mirror success.
printf '%s\t%s\t%s\n' "$primary" fail "$good" > "$FAKE_CURL_CONF"
printf '%s\t%s\t%s\n' "$mirror" ok "$good" >> "$FAKE_CURL_CONF"
: > "$FAKE_CURL_LOG"
dest="$tmp/d1.bin"
download_verified "OpenClaw installer" "$expected" 2000000 "$dest" "" "$primary" "$mirror"
[[ "$(cat "$dest")" == "$(cat "$good")" ]] || { echo "mirror bytes were not used" >&2; exit 1; }
grep -Fq "$primary" "$FAKE_CURL_LOG" || { echo "primary was not attempted" >&2; exit 1; }
grep -Fq "$mirror" "$FAKE_CURL_LOG" || { echo "mirror was not attempted" >&2; exit 1; }

# 2. Primary wrong SHA-256 then mirror success (mismatched bytes never accepted).
printf '%s\t%s\t%s\n' "$primary" ok "$bad" > "$FAKE_CURL_CONF"
printf '%s\t%s\t%s\n' "$mirror" ok "$good" >> "$FAKE_CURL_CONF"
: > "$FAKE_CURL_LOG"
dest="$tmp/d2.bin"
download_verified "OpenClaw installer" "$expected" 2000000 "$dest" "" "$primary" "$mirror"
[[ "$(cat "$dest")" == "$(cat "$good")" ]] || { echo "tampered primary bytes were accepted" >&2; exit 1; }
grep -Fq "$primary" "$FAKE_CURL_LOG" || { echo "tampered primary was not attempted" >&2; exit 1; }

# 3. All candidates fail -> overall failure and failed download is removed.
printf '%s\t%s\t%s\n' "$primary" fail "$good" > "$FAKE_CURL_CONF"
printf '%s\t%s\t%s\n' "$mirror" fail "$good" >> "$FAKE_CURL_CONF"
: > "$FAKE_CURL_LOG"
dest="$tmp/d3.bin"
fail_expected "$tmp/out3" "$tmp/err3" "OpenClaw installer" "$expected" 2000000 "$dest" "" "$primary" "$mirror"
grep -Fq "download failed after all candidates" "$tmp/err3"
[[ ! -e "$dest" ]] || { echo "failed download was not removed" >&2; exit 1; }

# 4. Non-HTTPS mirror is rejected and never attempted.
printf '%s\t%s\t%s\n' "$primary" fail "$good" > "$FAKE_CURL_CONF"
printf '%s\t%s\t%s\n' "http://insecure.example/install.sh" ok "$good" >> "$FAKE_CURL_CONF"
: > "$FAKE_CURL_LOG"
dest="$tmp/d4.bin"
fail_expected "$tmp/out4" "$tmp/err4" "OpenClaw installer" "$expected" 2000000 "$dest" "" "$primary" "http://insecure.example/install.sh"
grep -Fq "not HTTPS" "$tmp/out4"
! grep -Fq "http://insecure.example/install.sh" "$FAKE_CURL_LOG" || { echo "non-HTTPS mirror was attempted" >&2; exit 1; }

# 5. No candidates fails cleanly.
fail_expected "$tmp/out5" "$tmp/err5" "OpenClaw installer" "$expected" 2000000 "$tmp/d5.bin" ""
grep -Fq "no HTTPS download candidate" "$tmp/err5"

# 6. Malformed pins fail before any curl and leave no accepted destination,
#    even when the candidate list is empty or non-HTTPS.
: > "$FAKE_CURL_LOG"
dest="$tmp/d6a.bin"
fail_expected "$tmp/out6a" "$tmp/err6a" "OpenClaw installer" "not-a-64-hex-sha" 2000000 "$dest" ""
[[ ! -e "$dest" ]] || { echo "malformed-SHA download left a destination" >&2; exit 1; }
[[ ! -s "$FAKE_CURL_LOG" ]] || { echo "malformed SHA was not rejected before curl" >&2; exit 1; }
grep -Fq "invalid pinned SHA-256" "$tmp/err6a"
: > "$FAKE_CURL_LOG"
dest="$tmp/d6b.bin"
fail_expected "$tmp/out6b" "$tmp/err6b" "OpenClaw package" "$expected" 30000000 "$dest" "sha512-not-valid-grammar" "http://insecure.example/pkg.tgz"
[[ ! -e "$dest" ]] || { echo "malformed-integrity download left a destination" >&2; exit 1; }
[[ ! -s "$FAKE_CURL_LOG" ]] || { echo "malformed integrity was not rejected before curl" >&2; exit 1; }
grep -Fq "invalid pinned npm integrity" "$tmp/err6b"

echo "bootstrap mirror-fallback test passed"
