#!/usr/bin/env bash
# The uninstaller must stop with a clear message, before touching anything,
# when a user runs it through sudo; a direct root run is not affected.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_ROOT="$(mktemp -d)"
trap 'rm -rf -- "${TEST_ROOT:?}"' EXIT
PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); printf 'PASS: %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL: %s\n' "$1" >&2; }

mkdir -p "$TEST_ROOT/bin"
cat > "$TEST_ROOT/bin/id" <<'SH'
#!/usr/bin/env bash
if [[ "$1" == -u ]]; then echo 0; else exec /usr/bin/id "$@"; fi
SH
for tool in docker systemctl sudo; do
    printf '#!/usr/bin/env bash\necho "%s $*" >> "$CALLS"\n' "$tool" > "$TEST_ROOT/bin/$tool"
done
chmod +x "$TEST_ROOT/bin/"*

run_uninstaller() {
    local calls="$1"
    shift
    env -i PATH="$TEST_ROOT/bin:/usr/bin:/bin" HOME=/root CALLS="$calls" "$@" \
        bash "$ROOT_DIR/ods-uninstall.sh" --force --install-dir "$TEST_ROOT/missing" 2>&1
}

out="$(run_uninstaller "$TEST_ROOT/sudo.calls" SUDO_USER=alice)" && rc=0 || rc=$?
if [[ "$rc" == 1 && "$out" == *"Run this uninstaller as alice, without sudo"* \
    && ! -e "$TEST_ROOT/sudo.calls" ]]; then
    pass "a sudo run stops before any check or command"
else
    fail "a sudo run went on (rc=$rc): $out"
fi

out="$(run_uninstaller "$TEST_ROOT/root.calls")" && rc=0 || rc=$?
if [[ "$out" != *"without sudo"* ]]; then
    pass "a direct root run is not refused by the sudo check"
else
    fail "a direct root run was refused by the sudo check"
fi

out="$(run_uninstaller "$TEST_ROOT/sudo-root.calls" SUDO_USER=root)" && rc=0 || rc=$?
if [[ "$out" != *"without sudo"* ]]; then
    pass "sudo from root itself is not refused"
else
    fail "sudo from root itself was refused"
fi

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" == 0 ]]
