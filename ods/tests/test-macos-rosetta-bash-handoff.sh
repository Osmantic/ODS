#!/usr/bin/env bash
# An Apple Silicon Mac whose Homebrew is the Intel one (run by Rosetta) must
# still install natively, not stop with "Apple Silicon (arm64) is required.
# Detected: x86_64" (#3256).
#
# install-macos.sh hands off from macOS's Bash 3.2 to a Homebrew Bash 4+. It
# took the first one it found, so the Intel build in /usr/local ran the whole
# install under Rosetta, where `uname -m` says x86_64. A shell that Rosetta
# already ran (an Intel bash first on PATH) skipped the hand-off entirely.
#
# This runs the installer's real hand-off code with stand-ins for sysctl,
# uname, arch and brew and fake Homebrew trees, and reports which bash it ends
# in and as which architecture. It also checks get_apple_silicon_info under
# Rosetta.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLER="$ROOT_DIR/installers/macos/install-macos.sh"
REAL_BASH="$(command -v bash)"
REAL_UNAME="$(command -v uname)"

if ! "$REAL_BASH" -c '[ "${BASH_VERSINFO[0]}" -ge 4 ]'; then
    echo "[SKIP] the fake Homebrew bashes need Bash 4+ underneath; $REAL_BASH is older"
    exit 0
fi

PASS=0
FAIL=0
pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }
has() { printf '%s\n' "$1" | grep -qF -- "$2"; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin" "$TMP/templates"

# --- Stand-ins for the Mac -----------------------------------------------------
# FAKE_HW_ARM64=1 is an Apple Silicon Mac, "" an Intel one. FAKE_UNAME_M is the
# architecture the current process runs as: arm64 natively, x86_64 under
# Rosetta (or on an Intel Mac).
cat > "$TMP/bin/sysctl" <<EOF
#!$REAL_BASH
if [ "\${FAKE_HW_ARM64:-}" = 1 ]; then
    case "\$*" in
        "-n hw.optional.arm64") echo 1; exit 0 ;;
        "-n sysctl.proc_translated")
            if [ "\$FAKE_UNAME_M" = x86_64 ]; then echo 1; else echo 0; fi
            exit 0 ;;
    esac
fi
case "\$*" in
    "-n machdep.cpu.brand_string") echo "Apple M1"; exit 0 ;;
esac
echo "sysctl: unknown oid '\${2:-}'" >&2
exit 1
EOF

cat > "$TMP/bin/uname" <<EOF
#!$REAL_BASH
if [ "\${1:-}" = -m ]; then
    echo "\${FAKE_UNAME_M:?}"
    exit 0
fi
exec "$REAL_UNAME" "\$@"
EOF

# arch -arm64 PROGRAM [ARGS]: macOS refuses an Intel-only program and runs any
# other one natively.
cat > "$TMP/bin/arch" <<EOF
#!$REAL_BASH
echo "arch \$*" >> "$TMP/arch.log"
[ "\${1:-}" = -arm64 ] || { echo "arch stand-in: unexpected arguments: \$*" >&2; exit 64; }
shift
if grep -qx '# kind: x86_64' "\$1"; then
    echo "arch: posix_spawnp: \$1: Bad CPU type in executable" >&2
    exit 1
fi
export FAKE_UNAME_M=arm64
exec "\$@"
EOF
chmod +x "$TMP/bin/sysctl" "$TMP/bin/uname" "$TMP/bin/arch"

# A Homebrew Bash 5 (this bash underneath). An arm64 build always runs
# natively, an x86_64 build always under Rosetta, and a universal build keeps
# the architecture it was started with. FAKE_BASH_SELF names the bash the
# installer ended in; a hand-off loop stops instead of spinning.
for kind in arm64 x86_64 universal; do
    cat > "$TMP/templates/bash-$kind" <<EOF
#!$REAL_BASH
# kind: $kind
unset FAKE_START_MAJOR
case $kind in
    arm64|x86_64) export FAKE_UNAME_M=$kind ;;
esac
export FAKE_BASH_SELF="\$0"
export FAKE_HANDOFFS=\$(( \${FAKE_HANDOFFS:-0} + 1 ))
if [ "\$FAKE_HANDOFFS" -gt 20 ]; then
    echo "hand-off loop" >&2
    exit 99
fi
exec "$REAL_BASH" "\$@"
EOF
done

make_bash() {  # $1 = path, $2 = arm64 | x86_64 | universal
    mkdir -p "$(dirname "$1")"
    cp "$TMP/templates/bash-$2" "$1"
    chmod +x "$1"
}

# brew in PREFIX installs a bash of KIND. Like Homebrew, it refuses to install
# for the other architecture: an Intel prefix from a native shell, or the ARM
# prefix under Rosetta.
make_brew() {
    local prefix="$1" kind="$2"
    mkdir -p "$prefix/bin"
    cat > "$prefix/bin/brew" <<EOF
#!$REAL_BASH
case "\${1:-}" in
    --prefix)
        echo "$prefix"
        ;;
    install)
        echo "brew $prefix install \${2:-} as \$FAKE_UNAME_M" >> "$TMP/brew.log"
        if [ "$kind" = arm64 ] && [ "\$FAKE_UNAME_M" != arm64 ]; then
            echo "Error: Cannot install under Rosetta 2 in ARM default prefix ($prefix)!" >&2
            exit 1
        fi
        if [ "$kind" = x86_64 ] && [ "\$FAKE_UNAME_M" = arm64 ]; then
            echo "Error: Cannot install in Homebrew on ARM processor in Intel default prefix ($prefix)!" >&2
            exit 1
        fi
        cp "$TMP/templates/bash-$kind" "$prefix/bin/bash"
        chmod +x "$prefix/bin/bash"
        ;;
    *)
        exit 1
        ;;
esac
EOF
    chmod +x "$prefix/bin/brew"
}

# --- The installer's hand-off, verbatim up to `set -euo pipefail` ----------------
set_line="$(awk '/^set -euo pipefail$/ { print NR; exit }' "$INSTALLER")"
[[ "$set_line" =~ ^[0-9]+$ ]] || { echo "[FAIL] the Bash hand-off was not found in $INSTALLER"; exit 1; }
bootstrap="$(head -n "$((set_line - 1))" "$INSTALLER")"
# A scenario can start from macOS's Bash 3.2 (FAKE_START_MAJOR=3). The fake
# Homebrew bashes clear it, so only the starting shell counts as old.
old_guard='[ "${BASH_VERSINFO[0]:-0}" -lt 4 ]'
new_guard='[ "${FAKE_START_MAJOR:-${BASH_VERSINFO[0]:-0}}" -lt 4 ]'
[[ "$bootstrap" == *"$old_guard"* ]] || { echo "[FAIL] the Bash version guard was not found in $INSTALLER"; exit 1; }
bootstrap="${bootstrap//"$old_guard"/"$new_guard"}"
# Both Homebrew prefixes live under the test directory.
for prefix in /opt/homebrew /usr/local; do
    bootstrap="${bootstrap//"$prefix"/"$TMP$prefix"}"
done
{
    printf '%s\n' "$bootstrap"
    printf '%s\n' 'echo "handoff: bash=${FAKE_BASH_SELF:-starting shell} arch=$(uname -m)"' 'exit 0'
} > "$TMP/bootstrap.sh"

NATIVE="$TMP/opt/homebrew"
INTEL="$TMP/usr/local"
reset_mac() { rm -rf "$TMP/opt" "$TMP/usr" "$TMP/arch.log" "$TMP/brew.log"; }
brew_log() { if [[ -f "$TMP/brew.log" ]]; then cat "$TMP/brew.log"; fi; }

OUT=""
RC=0
run_installer() {
    # $1 = FAKE_HW_ARM64, $2 = architecture the installer starts as, $3 = Bash
    # major of the starting shell ("" = this bash), $4 = Homebrew bin
    # directories on PATH, first wins.
    local path="$TMP/bin"
    if [[ -n "$4" ]]; then
        path="$path:$4"
    fi
    RC=0
    OUT="$(cd "$TMP" && env PATH="$path:/usr/bin:/bin" FAKE_HW_ARM64="$1" FAKE_UNAME_M="$2" \
        FAKE_START_MAJOR="$3" FAKE_HANDOFFS=0 "$REAL_BASH" "$TMP/bootstrap.sh" 2>&1)" || RC=$?
}

# --- Apple Silicon ---------------------------------------------------------------
# The reported Mac: the Intel Homebrew is first on PATH with its Bash 5, and the
# native Homebrew has an arm64 Bash 5. The installer starts under Bash 3.2.
reset_mac
make_brew "$INTEL" x86_64
make_bash "$INTEL/bin/bash" x86_64
make_brew "$NATIVE" arm64
make_bash "$NATIVE/bin/bash" arm64
run_installer 1 arm64 3 "$INTEL/bin:$NATIVE/bin"
if [[ "$RC" -eq 0 ]] && has "$OUT" "handoff: bash=$NATIVE/bin/bash arch=arm64"; then
    pass "Bash 3.2, Intel and native Homebrew: hands off to the native bash, as arm64"
else
    fail "Bash 3.2, Intel and native Homebrew: must hand off to $NATIVE/bin/bash as arm64 (exit $RC): $OUT"
fi

# The Intel bash, first on PATH, started the installer: Rosetta already runs it
# and it is Bash 5, so the old guard never handed off.
run_installer 1 x86_64 "" "$INTEL/bin:$NATIVE/bin"
if [[ "$RC" -eq 0 ]] && has "$OUT" "handoff: bash=$NATIVE/bin/bash arch=arm64"; then
    pass "started by the Intel Bash 5 under Rosetta: hands off to the native bash, as arm64"
else
    fail "started by the Intel Bash 5 under Rosetta: must hand off to $NATIVE/bin/bash as arm64 (exit $RC): $OUT"
fi

# A universal bash keeps Rosetta's architecture unless started as arm64.
reset_mac
make_brew "$NATIVE" arm64
make_bash "$NATIVE/bin/bash" universal
run_installer 1 x86_64 3 "$NATIVE/bin"
if [[ "$RC" -eq 0 ]] && has "$OUT" "handoff: bash=$NATIVE/bin/bash arch=arm64"; then
    pass "universal bash from a Rosetta shell: runs as arm64"
else
    fail "universal bash from a Rosetta shell: must run as arm64 (exit $RC): $OUT"
fi

# Only the Intel Homebrew: no native bash is possible, so stop and say how to
# get the native Homebrew. Nothing is installed with the Intel one.
reset_mac
make_brew "$INTEL" x86_64
make_bash "$INTEL/bin/bash" x86_64
run_installer 1 arm64 3 "$INTEL/bin"
if [[ "$RC" -ne 0 ]] && ! has "$OUT" "handoff:"; then
    pass "Intel Homebrew only: stops instead of running the install under Rosetta"
else
    fail "Intel Homebrew only: must stop instead of handing off (exit $RC): $OUT"
fi
if has "$OUT" "Intel one in $INTEL" && has "$OUT" "arch -arm64 /bin/bash -c" \
    && has "$OUT" "https://discord.gg/4ntNp9MAwC"; then
    pass "Intel Homebrew only: names the cause, the native Homebrew install command and the Discord link"
else
    fail "Intel Homebrew only: must name the cause, the native Homebrew install command and the Discord link: $OUT"
fi
if [[ -z "$(brew_log)" ]]; then
    pass "Intel Homebrew only: installs nothing with it"
else
    fail "Intel Homebrew only: must not install with it: $(brew_log)"
fi

# The native Homebrew has no bash yet and the Intel one is first on PATH:
# install bash with the native one, as arm64.
reset_mac
make_brew "$INTEL" x86_64
make_brew "$NATIVE" arm64
run_installer 1 arm64 3 "$INTEL/bin:$NATIVE/bin"
if [[ "$RC" -eq 0 ]] && has "$OUT" "handoff: bash=$NATIVE/bin/bash arch=arm64" \
    && [[ "$(brew_log)" == "brew $NATIVE install bash as arm64" ]]; then
    pass "no bash yet: installs it with the native Homebrew, then hands off natively"
else
    fail "no bash yet: must install with the native Homebrew and hand off natively (exit $RC): $OUT / $(brew_log)"
fi

# The same, started by the Intel bash under Rosetta: brew must run as arm64 too,
# since Homebrew refuses its ARM prefix under Rosetta.
reset_mac
make_brew "$INTEL" x86_64
make_bash "$INTEL/bin/bash" x86_64
make_brew "$NATIVE" arm64
run_installer 1 x86_64 "" "$INTEL/bin:$NATIVE/bin"
if [[ "$RC" -eq 0 ]] && has "$OUT" "handoff: bash=$NATIVE/bin/bash arch=arm64" \
    && [[ "$(brew_log)" == "brew $NATIVE install bash as arm64" ]]; then
    pass "no native bash, started under Rosetta: installs with the native Homebrew as arm64"
else
    fail "no native bash, started under Rosetta: must install as arm64 and hand off natively (exit $RC): $OUT / $(brew_log)"
fi

# Already a native Bash 4+: no hand-off and no arch.
reset_mac
make_brew "$NATIVE" arm64
make_bash "$NATIVE/bin/bash" arm64
run_installer 1 arm64 "" "$NATIVE/bin"
if [[ "$RC" -eq 0 ]] && has "$OUT" "handoff: bash=starting shell arch=arm64" && [[ ! -e "$TMP/arch.log" ]]; then
    pass "native Bash 4+: continues in place"
else
    fail "native Bash 4+: must continue in place without arch (exit $RC): $OUT"
fi

# --- Intel Mac: unchanged (the installer's preflight then reports it) -------------
reset_mac
make_brew "$INTEL" x86_64
make_bash "$INTEL/bin/bash" x86_64
run_installer "" x86_64 3 "$INTEL/bin"
if [[ "$RC" -eq 0 ]] && has "$OUT" "handoff: bash=$INTEL/bin/bash arch=x86_64" && [[ ! -e "$TMP/arch.log" ]]; then
    pass "Intel Mac: hands off to the Homebrew bash as before, without arch"
else
    fail "Intel Mac: must hand off to $INTEL/bin/bash as before, without arch (exit $RC): $OUT"
fi

reset_mac
make_brew "$INTEL" x86_64
run_installer "" x86_64 3 "$INTEL/bin"
if [[ "$RC" -eq 0 ]] && has "$OUT" "handoff: bash=$INTEL/bin/bash arch=x86_64" \
    && [[ "$(brew_log)" == "brew $INTEL install bash as x86_64" ]] && [[ ! -e "$TMP/arch.log" ]]; then
    pass "Intel Mac without bash: brew install bash as before, without arch"
else
    fail "Intel Mac without bash: must run brew install bash as before (exit $RC): $OUT / $(brew_log)"
fi

# --- get_apple_silicon_info -------------------------------------------------------
apple_silicon_info() {
    # $1 = FAKE_HW_ARM64, $2 = uname -m. Prints "APPLE_IS_APPLE_SILICON APPLE_ARCH".
    (
        export PATH="$TMP/bin:/usr/bin:/bin" FAKE_HW_ARM64="$1" FAKE_UNAME_M="$2"
        system_profiler() { :; }
        source "$ROOT_DIR/installers/macos/lib/detection.sh"
        get_apple_silicon_info
        echo "$APPLE_IS_APPLE_SILICON $APPLE_ARCH"
    )
}

info="$(apple_silicon_info 1 x86_64)"
if [[ "$info" == "true arm64" ]]; then
    pass "get_apple_silicon_info under Rosetta: still Apple Silicon (arm64)"
else
    fail "get_apple_silicon_info under Rosetta must report Apple Silicon (arm64), got '$info'"
fi
info="$(apple_silicon_info "" x86_64)"
if [[ "$info" == "false x86_64" ]]; then
    pass "get_apple_silicon_info on an Intel Mac: not Apple Silicon"
else
    fail "get_apple_silicon_info on an Intel Mac must not report Apple Silicon, got '$info'"
fi
info="$(apple_silicon_info 1 arm64)"
if [[ "$info" == "true arm64" ]]; then
    pass "get_apple_silicon_info natively: Apple Silicon (arm64)"
else
    fail "get_apple_silicon_info natively must report Apple Silicon (arm64), got '$info'"
fi

echo "Result: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
