#!/usr/bin/env bash
# Regression: failed bootstrap full-model downloads must preserve the .part
# file and report real progress so the next retry can resume.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$ROOT_DIR/scripts/bootstrap-upgrade.sh"

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

fakebin="$tmp/bin"
install_dir="$tmp/install"
mkdir -p "$fakebin" "$install_dir/data/models" "$install_dir/config/llama-server" "$install_dir/bin"

cat > "$fakebin/curl" <<'EOF'
#!/usr/bin/env bash
case " $* " in
  *" -sI "*)
    if [[ "${ODS_FAKE_NO_CONTENT_LENGTH:-0}" == "1" ]]; then
      printf 'HTTP/2 200\r\n\r\n'
      exit 0
    fi
    printf 'HTTP/2 200\r\ncontent-length: 100\r\n\r\n'
    exit 0
    ;;
esac

out=""
prev=""
for arg in "$@"; do
    if [[ "$prev" == "-o" ]]; then
        out="$arg"
        break
    fi
    prev="$arg"
done

[[ -n "$out" ]] || exit 2
for arg in "$@"; do
    case "$arg" in
        --retry|--retry-all-errors|--max-time)
            echo "unexpected curl internal retry/global timeout flag: $arg" >&2
            exit 64
            ;;
    esac
done
mkdir -p "$(dirname "$out")"
printf 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx' >> "$out"
exit 56
EOF
chmod +x "$fakebin/curl"

cat > "$fakebin/sleep" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$fakebin/sleep"

cat > "$install_dir/.env" <<'EOF'
GGUF_FILE=Bootstrap.gguf
LLM_MODEL=bootstrap-model
MAX_CONTEXT=8192
CTX_SIZE=8192
GPU_BACKEND=apple
EOF

printf 'bootstrap model\n' > "$install_dir/data/models/Bootstrap.gguf"
printf '999999\n' > "$install_dir/data/.llama-server.pid"

if grep -Eq -- '--retry|--retry-all-errors|--max-time[[:space:]]+3600' "$TARGET"; then
    fail "bootstrap-upgrade long GGUF curl should rely on script-level retry/resume, not curl internal retry"
fi
grep -q 'ods-bootstrap-upgrade-' "$TARGET" \
    || fail "bootstrap-upgrade lock must live outside install data so reinstall cannot erase it"
if grep -q 'local lock_dir="$INSTALL_DIR/data/bootstrap-upgrade.lock"' "$TARGET"; then
    fail "bootstrap-upgrade lock must not live under install data"
fi
grep -q 'write_status "downloading" "$percent" "$progress_bytes" "$total_bytes"' "$TARGET" \
    || fail "active bootstrap-status must write clamped progress bytes so UI progress cannot exceed 100%"

set +e
PATH="$fakebin:$PATH" ODS_BOOTSTRAP_DOWNLOAD_ATTEMPTS=2 bash "$TARGET" \
    "$install_dir" \
    "Full.gguf" \
    "https://example.invalid/Full.gguf" \
    "" \
    "full-model" \
    "32768" \
    "Bootstrap.gguf" \
    > "$tmp/bootstrap.log" 2>&1
rc=$?
set -e

[[ $rc -ne 0 ]] || fail "bootstrap-upgrade must exit non-zero after repeated curl failures"
[[ -f "$install_dir/data/models/Full.gguf.part" ]] \
    || fail "bootstrap-upgrade must preserve the partial download for resume"
[[ ! -f "$install_dir/data/models/Full.gguf" ]] \
    || fail "bootstrap-upgrade must not promote a failed partial download"
grep -q '"status": "failed"' "$install_dir/data/bootstrap-status.json" \
    || fail "bootstrap-upgrade must mark bootstrap-status failed"
grep -Eq '"bytesDownloaded": [1-9][0-9]*' "$install_dir/data/bootstrap-status.json" \
    || fail "failed bootstrap-status must preserve downloaded byte count"
grep -q '"bytesTotal": 100' "$install_dir/data/bootstrap-status.json" \
    || fail "failed bootstrap-status must preserve expected byte count"
grep -q '"percent": 100.0' "$install_dir/data/bootstrap-status.json" \
    || fail "failed bootstrap-status percent must be capped at 100"
grep -Eqi 'preserved.*partial file.*resume|partial file.*preserved.*resume' "$tmp/bootstrap.log" \
    || fail "bootstrap-upgrade should tell operators the partial file was preserved"

unknown_install_dir="$tmp/install-unknown-size"
mkdir -p "$unknown_install_dir/data/models" "$unknown_install_dir/config/llama-server" "$unknown_install_dir/bin"
cp "$install_dir/.env" "$unknown_install_dir/.env"
printf 'bootstrap model\n' > "$unknown_install_dir/data/models/Bootstrap.gguf"
printf '999999\n' > "$unknown_install_dir/data/.llama-server.pid"

set +e
PATH="$fakebin:$PATH" ODS_FAKE_NO_CONTENT_LENGTH=1 ODS_BOOTSTRAP_DOWNLOAD_ATTEMPTS=1 bash "$TARGET" \
    "$unknown_install_dir" \
    "Full.gguf" \
    "https://example.invalid/Full.gguf" \
    "" \
    "full-model" \
    "32768" \
    "Bootstrap.gguf" \
    > "$tmp/bootstrap-unknown-size.log" 2>&1
unknown_rc=$?
set -e

[[ $unknown_rc -ne 0 ]] || fail "bootstrap-upgrade unknown-size download must exit non-zero after curl failure"
if grep -q 'progress_bytes: unbound variable' "$tmp/bootstrap-unknown-size.log"; then
    fail "bootstrap-upgrade monitor must not trip set -u when remote size is unknown"
fi
grep -q '"bytesTotal": 0' "$unknown_install_dir/data/bootstrap-status.json" \
    || fail "unknown-size bootstrap-status must preserve bytesTotal=0"
grep -Eq '"bytesDownloaded": [1-9][0-9]*' "$unknown_install_dir/data/bootstrap-status.json" \
    || fail "unknown-size bootstrap-status must preserve downloaded byte count"

pass "failed download preserves resumable partial and status progress"
