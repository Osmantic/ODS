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
grep -q 'ODS_BOOTSTRAP_DOWNLOAD_SPEED_LIMIT' "$TARGET" \
    || fail "bootstrap-upgrade download speed floor must be operator-configurable"
grep -q 'ODS_BOOTSTRAP_DOWNLOAD_HTTP_VERSION' "$TARGET" \
    || fail "bootstrap-upgrade HTTP transport must be operator-configurable"
grep -q 'ODS_BOOTSTRAP_DOWNLOAD_MAX_SECONDS' "$TARGET" \
    || fail "bootstrap-upgrade detached download should self-retry within a bounded wall-clock budget"
grep -q 'Retrying download in ${_download_retry_backoff_seconds}s; partial file preserved for resume.' "$TARGET" \
    || fail "bootstrap-upgrade retry exhaustion should keep status active before the retry budget expires"
grep -q -- '--http1.1' "$TARGET" \
    || fail "bootstrap-upgrade should prefer HTTP/1.1 after observed HuggingFace HTTP/2 stream cancels"
if grep -q -- '--speed-time 300 --speed-limit 1024' "$TARGET"; then
    fail "bootstrap-upgrade must not allow multi-day stalled model downloads by default"
fi
grep -q 'ods-bootstrap-upgrade-' "$TARGET" \
    || fail "bootstrap-upgrade lock must live outside install data so reinstall cannot erase it"
if grep -q 'local lock_dir="$INSTALL_DIR/data/bootstrap-upgrade.lock"' "$TARGET"; then
    fail "bootstrap-upgrade lock must not live under install data"
fi
grep -q 'write_status "downloading" "$percent" "$progress_bytes" "$total_bytes"' "$TARGET" \
    || fail "active bootstrap-status must write clamped progress bytes so UI progress cannot exceed 100%"
if grep -q 'write_status "downloading" "" 0 0 0 "Another bootstrap model upgrade is already running' "$TARGET"; then
    fail "duplicate bootstrap-upgrade path must not flatten active download status to zero progress"
fi
grep -q 'write_existing_upgrade_status "$existing_pid"' "$TARGET" \
    || fail "duplicate bootstrap-upgrade path must reconstruct active download progress"
grep -q '_ods_cli_bootstrap_upgrade_status_is_stale' "$ROOT_DIR/ods-cli" \
    || fail "ods-cli must detect stale active bootstrap upgrades"
grep -q 'stale download appears stopped' "$ROOT_DIR/ods-cli" \
    || fail "ods-cli must retry stale active bootstrap upgrades without marking live downloads failed"
grep -q 'ODS_BOOTSTRAP_UPGRADE_STALE_SECONDS' "$ROOT_DIR/ods-cli" \
    || fail "ods-cli stale bootstrap retry window must be operator-configurable"

locked_install_dir="$tmp/install-lock-held"
mkdir -p "$locked_install_dir/data/models" "$locked_install_dir/config/llama-server" "$locked_install_dir/bin" "$tmp/locks"
cp "$install_dir/.env" "$locked_install_dir/.env"
printf 'bootstrap model\n' > "$locked_install_dir/data/models/Bootstrap.gguf"
printf 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx' \
    > "$locked_install_dir/data/models/Full.gguf.part"

sleep 30 &
existing_pid=$!
trap 'kill "$existing_pid" 2>/dev/null || true; rm -rf "$tmp"' EXIT
lock_key="$(printf '%s\0%s' "$locked_install_dir" "Full.gguf" | cksum | awk '{print $1}')"
lock_dir="$tmp/locks/ods-bootstrap-upgrade-${lock_key}.lock"
mkdir -p "$lock_dir"
printf '%s\n' "$existing_pid" > "$lock_dir/pid"

PATH="$fakebin:$PATH" TMPDIR="$tmp/locks" bash "$TARGET" \
    "$locked_install_dir" \
    "Full.gguf" \
    "https://example.invalid/Full.gguf" \
    "" \
    "full-model" \
    "32768" \
    "Bootstrap.gguf" \
    > "$tmp/bootstrap-lock-held.log" 2>&1

grep -q '"status": "downloading"' "$locked_install_dir/data/bootstrap-status.json" \
    || fail "lock-held bootstrap-status must remain downloading"
grep -q '"bytesDownloaded": 60' "$locked_install_dir/data/bootstrap-status.json" \
    || fail "lock-held bootstrap-status must report existing partial bytes"
grep -q '"bytesTotal": 100' "$locked_install_dir/data/bootstrap-status.json" \
    || fail "lock-held bootstrap-status must report remote size when available"
grep -q '"percent": 60.0' "$locked_install_dir/data/bootstrap-status.json" \
    || fail "lock-held bootstrap-status must report existing partial progress"
grep -q "Continuing existing bootstrap model upgrade (pid $existing_pid)" \
    "$locked_install_dir/data/bootstrap-status.json" \
    || fail "lock-held bootstrap-status should identify the controlling upgrade"
kill "$existing_pid" 2>/dev/null || true

set +e
PATH="$fakebin:$PATH" ODS_BOOTSTRAP_DOWNLOAD_ATTEMPTS=2 ODS_BOOTSTRAP_DOWNLOAD_MAX_SECONDS=0 bash "$TARGET" \
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
PATH="$fakebin:$PATH" ODS_FAKE_NO_CONTENT_LENGTH=1 ODS_BOOTSTRAP_DOWNLOAD_ATTEMPTS=1 ODS_BOOTSTRAP_DOWNLOAD_MAX_SECONDS=0 bash "$TARGET" \
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
