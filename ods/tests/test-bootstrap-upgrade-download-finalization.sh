#!/usr/bin/env bash
# Regression: bootstrap-upgrade must not promote .env or delete the bootstrap
# GGUF unless the downloaded .part file was successfully finalized.

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
    printf 'HTTP/2 200\r\ncontent-length: 1234\r\n\r\n'
    exit 0
    ;;
esac
# Simulate the macOS fleet failure shape: curl exits 0, but the expected
# .part file is not present by the time bootstrap-upgrade tries to promote it.
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

set +e
PATH="$fakebin:$PATH" bash "$TARGET" \
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

[[ $rc -ne 0 ]] || fail "bootstrap-upgrade must exit non-zero when curl succeeds but no finalized GGUF exists"
grep -q 'produced no partial file' "$tmp/bootstrap.log" \
    || fail "bootstrap-upgrade should log the missing .part/finalization failure"
grep -q '^GGUF_FILE=Bootstrap.gguf$' "$install_dir/.env" \
    || fail "bootstrap-upgrade must leave GGUF_FILE on the bootstrap model after download finalization failure"
grep -q '^LLM_MODEL=bootstrap-model$' "$install_dir/.env" \
    || fail "bootstrap-upgrade must leave LLM_MODEL unchanged after download finalization failure"
[[ -f "$install_dir/data/models/Bootstrap.gguf" ]] \
    || fail "bootstrap-upgrade must not delete the serving bootstrap GGUF after download finalization failure"
[[ ! -f "$install_dir/data/models/Full.gguf" ]] \
    || fail "bootstrap-upgrade must not leave a bogus full GGUF after download finalization failure"
grep -q '"status": "failed"' "$install_dir/data/bootstrap-status.json" \
    || fail "bootstrap-upgrade must mark bootstrap-status failed"

pass "download finalization failure is non-destructive"
