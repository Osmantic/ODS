#!/usr/bin/env bash
# Regression: a completed but corrupt bootstrap full-model download should be
# retried from a clean file before the upgrader gives up.

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
    printf 'HTTP/2 200\r\ncontent-length: 4\r\n\r\n'
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
mkdir -p "$(dirname "$out")"
printf 'bad!' > "$out"
printf '.' >> "$ODS_FAKE_CURL_COUNT"
exit 0
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
count_file="$tmp/curl-count"
: > "$count_file"
expected_sha="$(printf 'good' | sha256sum | awk '{print $1}')"

set +e
PATH="$fakebin:$PATH" ODS_FAKE_CURL_COUNT="$count_file" ODS_BOOTSTRAP_DOWNLOAD_ATTEMPTS=2 ODS_BOOTSTRAP_DOWNLOAD_MAX_SECONDS=0 bash "$TARGET" \
    "$install_dir" \
    "Full.gguf" \
    "https://example.invalid/Full.gguf" \
    "$expected_sha" \
    "full-model" \
    "32768" \
    "Bootstrap.gguf" \
    > "$tmp/bootstrap.log" 2>&1
rc=$?
set -e

[[ $rc -ne 0 ]] || fail "bootstrap-upgrade must exit non-zero after repeated SHA failures"
[[ "$(wc -c < "$count_file" | tr -d ' ')" == "2" ]] \
    || fail "bootstrap-upgrade should retry a SHA-failed download from scratch"
[[ ! -f "$install_dir/data/models/Full.gguf" ]] \
    || fail "bootstrap-upgrade must delete the corrupt final model"
[[ ! -f "$install_dir/data/models/Full.gguf.part" ]] \
    || fail "bootstrap-upgrade must not preserve a checksum-failed partial as resumable"
grep -q 'Integrity verification failed on attempt 1' "$tmp/bootstrap.log" \
    || fail "bootstrap-upgrade should log the SHA retry"
grep -q '"status": "failed"' "$install_dir/data/bootstrap-status.json" \
    || fail "bootstrap-upgrade must mark bootstrap-status failed after SHA retries are exhausted"

pass "SHA-failed full-model downloads retry from a clean file"
