#!/usr/bin/env bash
# pre-download.sh used to fetch Hugging Face *snapshots* (Qwen/Qwen2.5-*) into
# ~/.cache/huggingface/hub — a cache nothing in the stack reads. The installer
# serves a single GGUF per tier (installers/lib/tier-map.sh) from
# $INSTALL_DIR/data/models, and Phase 06's rsync excludes data/ entirely, so
# every byte pre-download.sh fetched was discarded and install.sh re-downloaded
# the real model anyway. FAQ.md also documents `--tier 3`, which the old tier
# table rejected as unknown.
#
# This test asserts the fixed contract:
#   1. `--tier <installer tier id>` resolves via tier-map.sh and downloads the
#      tier's GGUF into $INSTALL_DIR/data/models.
#   2. The fetched URL is the tier-map GGUF_URL (single source of truth).
#   3. --verify reports the downloaded file as cached.
if [ "${BASH_VERSINFO[0]:-0}" -lt 4 ]; then
    for candidate in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        [[ -x "$candidate" ]] && exec "$candidate" "$0" "$@"
    done
    echo "[SKIP] pre-download.sh requires Bash 4+; this host only has Bash ${BASH_VERSION}"
    echo "Result: 0 passed, 0 failed, 1 skipped"
    exit 0
fi
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${ODS_PRE_DOWNLOAD_UNDER_TEST:-$ROOT_DIR/scripts/pre-download.sh}"
TIER_MAP="$ROOT_DIR/installers/lib/tier-map.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

[[ -f "$TARGET" ]] || fail "missing $TARGET"
[[ -f "$TIER_MAP" ]] || fail "missing $TIER_MAP"

# Expected model comes from the same source of truth the installer uses, so
# the test stays correct when tier-map assignments change.
MODEL_PROFILE=qwen
# shellcheck disable=SC2034  # TIER is read by tier-map.sh after sourcing
TIER=3
. "$TIER_MAP"
error() { echo "[ERROR] $*" >&2; }
resolve_tier_config
[[ -n "${GGUF_FILE:-}" && -n "${GGUF_URL:-}" ]] || fail "tier-map has no GGUF for tier 3"
EXPECTED_FILE="$GGUF_FILE"
EXPECTED_URL="$GGUF_URL"

export INSTALL_DIR="$TMP_DIR/install" MODEL_PROFILE=qwen HOST_ARCH=x86_64

BIN="$TMP_DIR/bin"; mkdir -p "$BIN"
cat > "$BIN/curl" <<'EOF'
#!/bin/sh
# Stub curl: record the URL, create the -o target so .part rename succeeds.
out="" url=""
while [ $# -gt 0 ]; do
    case "$1" in
        -o) out="$2"; shift 2 ;;
        http*|https*) url="$1"; shift ;;
        *)  shift ;;
    esac
done
[ -n "$url" ] && echo "$url" >> "${CURL_LOG:-/dev/null}"
[ -n "$out" ] && { mkdir -p "$(dirname "$out")"; : > "$out"; }
exit 0
EOF
cat > "$BIN/python3" <<'EOF'
#!/bin/sh
if [ "$1" = "-c" ]; then exit 0; fi
cat >/dev/null 2>&1 || true
exit 0
EOF
# The stub file can't satisfy a real checksum; return the tier-map sha256 so
# the integrity path is exercised end-to-end.
cat > "$BIN/sha256sum" <<EOF
#!/bin/sh
echo "${GGUF_SHA256:-none}  \$1"
EOF
chmod +x "$BIN/curl" "$BIN/python3" "$BIN/sha256sum"

export CURL_LOG="$TMP_DIR/urls.txt"
out="$TMP_DIR/out.txt"
set +e
PATH="$BIN:$PATH" bash "$TARGET" --tier 3 </dev/null >"$out" 2>&1
rc=$?
set -e
[[ $rc -eq 0 ]] || fail "--tier 3 failed (rc=$rc): $(tail -n 3 "$out" | tr '\n' ' ')"
pass "--tier 3 (FAQ-documented numeric tier) completes"

DEST="$INSTALL_DIR/data/models/$EXPECTED_FILE"
[[ -f "$DEST" ]] || fail "GGUF not downloaded to install dir: missing $DEST — $(tail -n 3 "$out" | tr '\n' ' ')"
pass "tier-3 GGUF landed in \$INSTALL_DIR/data/models"

grep -qx "$EXPECTED_URL" "$CURL_LOG" \
    || fail "downloaded URL is not the tier-map GGUF_URL: $(cat "$CURL_LOG" 2>/dev/null | tail -n 1)"
pass "downloaded from the tier-map GGUF_URL"

out2="$TMP_DIR/out2.txt"
set +e
PATH="$BIN:$PATH" bash "$TARGET" --verify >"$out2" 2>&1
rc2=$?
set -e
[[ $rc2 -eq 0 ]] || fail "--verify failed (rc=$rc2)"
grep -q "✓ 3: $EXPECTED_FILE" "$out2" \
    || fail "--verify did not report the downloaded GGUF: $(grep -i 'tier 3' "$out2")"
pass "--verify reports the downloaded GGUF as cached"

echo "Result: 4 passed, 0 failed"
