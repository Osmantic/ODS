#!/usr/bin/env bash
# Execute the complete pre-download CLI with dependency/download shims.
# EOF must explain how to choose a tier, while explicit --tier remains usable
# without a terminal. Entered defaults and cancellation retain their behavior.
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
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

[[ -f "$TARGET" ]] || fail "missing $TARGET"

# Never import/install packages or contact Hugging Face. Record each download
# call so error and cancellation cases prove that no download was attempted.
BIN="$TMP_DIR/bin"; mkdir -p "$BIN"
cat > "$BIN/python3" <<'EOF'
#!/bin/sh
if [ "${1:-}" = "-c" ]; then
    case "${2:-}" in
        'import sys; sys.exit(0)'|'import huggingface_hub') exit 0 ;;
        *) exit 97 ;;
    esac
fi
[ "$#" -eq 0 ] || exit 98
cat >/dev/null
printf 'download\n' >> "$ODS_PRE_DOWNLOAD_TEST_LOG"
echo "Downloaded to: /tmp/fake-model-cache"
EOF
cat > "$BIN/pip3" <<'EOF'
#!/bin/sh
echo 'Unexpected package installation in pre-download regression test' >&2
exit 99
EOF
cat > "$BIN/nvidia-smi" <<'EOF'
#!/bin/sh
# Deterministic recommendation: pro, independent of host hardware.
printf '24576\n'
EOF
chmod +x "$BIN/python3" "$BIN/pip3" "$BIN/nvidia-smi"
# Fail before the target can select real dependencies on a noexec temp mount.
[[ $(PATH="$BIN:$PATH" command -v python3) == "$BIN/python3" ]] || fail 'python shim is not executable'
[[ $(PATH="$BIN:$PATH" command -v pip3) == "$BIN/pip3" ]] || fail 'pip shim is not executable'
"$BIN/python3" -c 'import huggingface_hub' || fail 'python shim did not execute'

run_case() {
    local name="$1" input="$2"
    shift 2
    out="$TMP_DIR/$name.out"
    err="$TMP_DIR/$name.err"
    downloads="$TMP_DIR/$name.downloads"
    : > "$downloads"
    set +e
    printf '%s' "$input" | PATH="$BIN:$PATH" ODS_PRE_DOWNLOAD_TEST_LOG="$downloads" \
        "$BASH" "$TARGET" "$@" >"$out" 2>"$err"
    rc=$?
    set -e
}

assert_no_download() {
    [[ ! -s "$downloads" ]] || fail "unexpected download: $(cat "$out")"
}

assert_eof_error() {
    [[ $rc -eq 1 ]] || fail "EOF should exit 1, got $rc"
    grep -qF "$1" "$err" || fail "missing prompt-specific error: $(cat "$err")"
    grep -qF -- '--tier' "$err" || fail 'missing non-interactive --tier guidance'
    assert_no_download
}

run_case closed-stdin ''
assert_eof_error 'No tier selection received.'
pass 'closed stdin reports how to select a tier without downloading'

run_case blank-line $'\n'
assert_eof_error 'No voice selection received.'
pass 'a blank tier line followed by EOF does not silently download the recommendation'

run_case tier-eof $'edge\n'
assert_eof_error 'No voice selection received.'
pass 'EOF at the voice prompt reports non-interactive usage without downloading'

run_case unterminated-tier 'edge'
assert_eof_error 'No tier selection received.'
pass 'an unterminated tier is not discarded in favor of a recommended download'

run_case typed-defaults $'\n\n\n'
[[ $rc -eq 0 ]] || fail "entered defaults failed: $(cat "$err")"
grep -qF 'Downloading pro tier models' "$out" || fail 'entered blank tier lost its recommended default'
[[ $(wc -l < "$downloads") -eq 1 ]] || fail 'entered defaults should download one LLM'
pass 'entered defaults retain the recommended tier without voice components'

run_case explicit-tier '' --tier nano
[[ $rc -eq 0 ]] || fail "explicit --tier failed under EOF: $(cat "$err")"
grep -qF 'Pre-download complete' "$out" || fail 'explicit tier did not complete'
[[ $(wc -l < "$downloads") -eq 1 ]] || fail 'explicit tier should download one LLM'
pass 'explicit --tier still completes with closed stdin'

run_case explicit-voice '' --tier nano --with-voice
[[ $rc -eq 0 ]] || fail "explicit voice selection failed: $(cat "$err")"
[[ $(wc -l < "$downloads") -eq 3 ]] || fail 'explicit voice should download LLM, STT and TTS'
pass 'explicit --with-voice still includes both voice models'

run_case cancel 'n' --tier nano
[[ $rc -eq 0 ]] || fail "declining should exit 0, got $rc"
grep -qF 'Cancelled' "$out" || fail "a piped 'n' should cancel the download"
assert_no_download
pass "a piped 'n' still cancels before downloading"

run_case invalid-tier '' --tier bogus
[[ $rc -eq 1 ]] || fail "invalid tier should exit 1, got $rc"
grep -qF 'Unknown tier: bogus' "$err" || fail 'invalid-tier validation was lost'
assert_no_download
pass 'invalid tier is still rejected before downloading'
