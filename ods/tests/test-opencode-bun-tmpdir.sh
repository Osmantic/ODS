#!/usr/bin/env bash
# OpenCode 1.18.x embeds Bun 1.3.14, which copies bundled native libraries to a
# new randomly named temp file on every load and never deletes them
# (anomalyco/opencode#42700, #49283). Every ODS-managed OpenCode launcher must
# confine those copies to an ODS-owned BUN_TMPDIR that is emptied on start.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE="$ROOT_DIR/opencode/opencode-web.service"
MACOS_INSTALLER="$ROOT_DIR/installers/macos/install-macos.sh"
MACOS_CONSTANTS="$ROOT_DIR/installers/macos/lib/constants.sh"
WINDOWS_PHASE="$ROOT_DIR/installers/windows/phases/07-devtools.ps1"

pass() { echo "[PASS] $1"; }
fail() { echo "[FAIL] $1" >&2; exit 1; }
mode_of() { python3 -c 'import os, stat, sys; print(oct(stat.S_IMODE(os.stat(sys.argv[1]).st_mode)))' "$1"; }
entries_in() { find "$1" -mindepth 1 -maxdepth 1 | wc -l | tr -d ' '; }
leak_into() { printf 'stale native library copy' > "$1/.0123456789abcdef-00000000.so"; }

scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT

echo "=== OpenCode BUN_TMPDIR confinement ==="

# --- Linux systemd user unit, rendered like installers/phases/07-devtools.sh ---
home="$scratch/home with space"
mkdir -p "$home"
unit="$scratch/opencode-web.service"
sed -e "s|__HOME__|$home|g" -e 's|__OPENCODE_BIN__|/opt/oc/opencode|g' \
    -e 's|__OPENCODE_BIN_DIR__|/opt/oc|g' "$SERVICE" > "$unit"
linux_dir="$home/.cache/ods/opencode-bun-tmp"

grep -Fxq "Environment=\"BUN_TMPDIR=$linux_dir\"" "$unit" \
    || fail "Linux unit must set an ODS-owned BUN_TMPDIR"
if grep -Eq '^Environment="?TMPDIR=' "$unit"; then
    fail "Linux unit must leave TMPDIR for OpenCode tools unchanged"
fi
grep -q '^PrivateTmp=true$' "$unit" || fail "Linux unit must keep PrivateTmp=true"

# Run the unit's own ExecStartPre/ExecStopPost commands (plain double-quoted
# arguments, which systemd and sh split identically).
run_directives() {
    local key="$1" line found=0
    while IFS= read -r line; do
        eval "${line#"$key="}"
        found=1
    done < <(grep "^$key=" "$unit")
    [[ "$found" == 1 ]] || fail "Linux unit is missing $key"
}

mkdir -p "$linux_dir"
leak_into "$linux_dir"
run_directives ExecStartPre
[[ -d "$linux_dir" && "$(entries_in "$linux_dir")" == 0 ]] \
    || fail "Linux ExecStartPre must leave an empty BUN_TMPDIR"
[[ "$(mode_of "$linux_dir")" == "0o700" ]] || fail "Linux BUN_TMPDIR must be private (0700)"
leak_into "$linux_dir"
run_directives ExecStopPost
[[ ! -e "$linux_dir" ]] || fail "Linux ExecStopPost must remove BUN_TMPDIR"
pass "Linux unit empties BUN_TMPDIR before start and removes it after stop"

# --- macOS LaunchAgent, rendered from the installer heredoc ---
# shellcheck disable=SC2016 # literal $HOME in the constants file
grep -Fxq 'OPENCODE_BUN_TMPDIR="$HOME/Library/Caches/ODS/opencode-bun-tmp"' "$MACOS_CONSTANTS" \
    || fail "macOS BUN_TMPDIR must live in the user's ODS cache"
mac_dir="$scratch/mac bun tmp"
fake_bin="$scratch/fake-opencode"
record="$scratch/fake-opencode.record"
cat > "$fake_bin" <<'FAKE'
#!/bin/sh
{
    printf 'BUN_TMPDIR=%s\n' "$BUN_TMPDIR"
    printf 'ENTRIES=%s\n' "$(find "$BUN_TMPDIR" -mindepth 1 -maxdepth 1 | wc -l | tr -d ' ')"
    printf 'ARGS=%s\n' "$*"
} > "$ODS_TEST_RECORD"
printf 'fresh copy' > "$BUN_TMPDIR/.fedcba9876543210-00000000.so"
FAKE
chmod +x "$fake_bin"

{
    echo 'cat <<PLIST_EOF'
    awk '/<<PLIST_EOF$/{inside=1; next} /^PLIST_EOF$/{inside=0} inside' "$MACOS_INSTALLER"
    echo 'PLIST_EOF'
} > "$scratch/render-plist.sh"
OPENCODE_PLIST_LABEL="com.ods.opencode-web" OPENCODE_BIN="$fake_bin" \
    OPENCODE_BUN_TMPDIR="$mac_dir" INSTALL_DIR="$scratch" HOME="$scratch" \
    OPENCODE_LAUNCHD_PATH="/usr/bin:/bin" bash "$scratch/render-plist.sh" > "$scratch/opencode.plist"

# plistlib also proves the rendered XML (escaping, comments) is a valid plist.
python3 - "$scratch/opencode.plist" > "$scratch/program-args.txt" <<'PY'
import plistlib, sys
with open(sys.argv[1], 'rb') as handle:
    plist = plistlib.load(handle)
for value in plist['ProgramArguments']:
    print(value)
PY
program_args=()
while IFS= read -r value; do
    program_args+=("$value")
done < "$scratch/program-args.txt"
[[ "${program_args[0]}" == "/bin/sh" && "${program_args[1]}" == "-c" \
    && "${program_args[3]}" == "ods-opencode-web" && "${program_args[4]}" == "$mac_dir" \
    && "${program_args[5]}" == "$fake_bin" ]] \
    || fail "macOS LaunchAgent must start OpenCode through the BUN_TMPDIR wrapper"
[[ "${program_args[*]:6}" == "web --port 3003 --hostname 127.0.0.1" ]] \
    || fail "macOS LaunchAgent must keep the loopback web command"

mkdir -p "$mac_dir"
leak_into "$mac_dir"
for start in 1 2; do
    ODS_TEST_RECORD="$record" "${program_args[@]}"
    grep -Fxq "BUN_TMPDIR=$mac_dir" "$record" || fail "macOS start $start must export BUN_TMPDIR"
    grep -Fxq "ENTRIES=0" "$record" || fail "macOS start $start must begin with an empty BUN_TMPDIR"
    grep -Fxq "ARGS=web --port 3003 --hostname 127.0.0.1" "$record" \
        || fail "macOS start $start must pass the web command unchanged"
done
[[ "$(entries_in "$mac_dir")" == 1 ]] || fail "macOS restarts must not accumulate copies"
[[ "$(mode_of "$mac_dir")" == "0o700" ]] || fail "macOS BUN_TMPDIR must be private (0700)"
pass "macOS LaunchAgent empties BUN_TMPDIR on every start and execs OpenCode"

# --- Windows scheduled-task launcher (literal PowerShell text) ---
# shellcheck disable=SC2016
set_line="$(grep -n 'env:BUN_TMPDIR = `\$bunTmp' "$WINDOWS_PHASE" | cut -d: -f1)"
# shellcheck disable=SC2016
remove_line="$(grep -n 'Remove-Item -LiteralPath `\$bunTmp -Recurse -Force' "$WINDOWS_PHASE" | cut -d: -f1)"
launch_line="$(grep -n "& '\$_ocExeLiteral' web --port" "$WINDOWS_PHASE" | cut -d: -f1)"
grep -q "Join-Path \`\$env:LOCALAPPDATA 'ODS\\\\opencode-bun-tmp'" "$WINDOWS_PHASE" \
    || fail "Windows launcher must use an ODS-owned BUN_TMPDIR"
[[ -n "$set_line" && -n "$remove_line" && -n "$launch_line" \
    && "$remove_line" -lt "$set_line" && "$set_line" -lt "$launch_line" ]] \
    || fail "Windows launcher must empty and export BUN_TMPDIR before starting OpenCode"
pass "Windows launcher empties BUN_TMPDIR before starting OpenCode"

echo "[PASS] OpenCode BUN_TMPDIR confinement"
