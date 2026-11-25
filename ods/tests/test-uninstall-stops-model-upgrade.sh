#!/usr/bin/env bash
# Contract: ods-uninstall.sh stops this installation's detached background
# model upgrade (bootstrap-upgrade.sh and its download child) before it
# changes anything, and leaves another installation's upgrade alone. A
# survivor kept downloading into, and later rewrote, the next install at the
# same path on macOS fleet hosts.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNINSTALL="$ROOT_DIR/ods-uninstall.sh"
command -v setsid >/dev/null || { echo "[SKIP] setsid unavailable"; exit 0; }

fail() { echo "[FAIL] $*"; exit 1; }
pass() { echo "[PASS] $*"; }

block="$(sed -n "/^# Stop this installation's background full-model upgrade/,/^fi$/p" "$UNINSTALL")"
[[ -n "$block" ]] || fail "could not extract the model-upgrade stop from ods-uninstall.sh"

TMP="$(mktemp -d)"
cleanup() {
    local pid
    for pid in $(pgrep -f "$TMP/" || true); do
        pkill -P "$pid" 2>/dev/null || true
        kill "$pid" 2>/dev/null || true
    done
    rm -rf -- "$TMP"
}
trap cleanup EXIT

make_upgrade() {
    mkdir -p "$1/scripts"
    # Like the real worker: a script whose download child outlives a plain
    # TERM of the script unless the whole group is signalled.
    cat > "$1/scripts/bootstrap-upgrade.sh" <<'EOF'
#!/usr/bin/env bash
sleep 300 &
wait
EOF
    chmod +x "$1/scripts/bootstrap-upgrade.sh"
}

ours="$TMP/ods"; theirs="$TMP/other-ods"
make_upgrade "$ours"; make_upgrade "$theirs"

# Newer installers: the worker leads its own process group.
setsid bash "$ours/scripts/bootstrap-upgrade.sh" "$ours" >/dev/null 2>&1 < /dev/null &
setsid bash "$theirs/scripts/bootstrap-upgrade.sh" "$theirs" >/dev/null 2>&1 < /dev/null &
ours_script=""; ours_child=""
for _ in $(seq 1 50); do
    ours_script="$(pgrep -f "$ours/scripts/bootstrap-upgrade.sh" || true)"
    [[ -n "$ours_script" ]] && ours_child="$(pgrep -P "$ours_script" || true)"
    [[ -n "$ours_child" ]] && break
    sleep 0.1
done
[[ -n "$ours_child" ]] || fail "fixture did not start the download child"

(
    # shellcheck disable=SC2034  # read by the evaluated uninstall block
    INSTALL_DIR="$ours"
    log_info() { echo "INFO $*"; }
    eval "$block"
) >"$TMP/out.log" 2>&1

kill -0 "$ours_script" 2>/dev/null && fail "this installation's upgrade script survived"
kill -0 "$ours_child" 2>/dev/null && fail "this installation's download child survived"
grep -q "Stopping the background model upgrade" "$TMP/out.log" || fail "the stop was not reported"
pgrep -f "$theirs/scripts/bootstrap-upgrade.sh" >/dev/null || fail "another installation's upgrade was stopped"
pass "group-leader upgrade and its download stopped; another installation's upgrade kept"

# Older installers: the worker is not a group leader; its children still go.
for pid in $(pgrep -f "$theirs/" || true); do pkill -P "$pid" 2>/dev/null || true; kill "$pid" 2>/dev/null || true; done
bash "$ours/scripts/bootstrap-upgrade.sh" "$ours" >/dev/null 2>&1 < /dev/null &
legacy_script=$!
for _ in $(seq 1 50); do
    [[ -n "$(pgrep -P "$legacy_script" 2>/dev/null)" ]] && break
    sleep 0.1
done
legacy_child="$(pgrep -P "$legacy_script")"
[[ -n "$legacy_child" ]] || fail "legacy fixture did not start the download child"
(
    # shellcheck disable=SC2034  # read by the evaluated uninstall block
    INSTALL_DIR="$ours"
    log_info() { echo "INFO $*"; }
    eval "$block"
) >"$TMP/out-legacy.log" 2>&1
wait "$legacy_script" 2>/dev/null || true
kill -0 "$legacy_child" 2>/dev/null && fail "legacy download child survived"
pass "legacy (non-leader) upgrade and its download stopped"

# Order: the stop runs before Windows startup or Pixel retirement mutates anything.
stop_line="$(grep -n "^# Stop this installation's background full-model upgrade" "$UNINSTALL" | cut -d: -f1)"
pixel_line="$(grep -n "^# Validate and remove Pixel before stopping" "$UNINSTALL" | cut -d: -f1)"
wsl_line="$(grep -n "^# Disable and settle the bound Windows login startup" "$UNINSTALL" | cut -d: -f1)"
(( stop_line < wsl_line && stop_line < pixel_line )) || fail "model-upgrade stop must precede the first uninstall mutation"
pass "model-upgrade stop precedes Windows startup and Pixel retirement"

echo "All uninstall model-upgrade checks passed."
