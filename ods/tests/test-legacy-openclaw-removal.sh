#!/usr/bin/env bash
# The legacy OpenClaw extension (the ods-openclaw container) was removed.
# Portal (Pixel) and Hermes are the supported agents.
#
# Scripts that still pass the old flags must keep working: every installer
# accepts them, prints one notice and changes nothing else. Upgrades must
# delete the stale extensions/services/openclaw tree (the source copy never
# prunes) and the untouched shipped templates in config/openclaw, while keeping
# every file the owner changed or added and data/openclaw. On Linux the prune
# must never run while an unfinished Pixel source plan is pending: that plan is
# bound to the exact tree it recorded.
#
# This runs the real Linux and macOS argument loops, prune code and the Linux
# Pixel source-plan gate against throwaway directories, and checks the Windows
# entry points statically.
set -euo pipefail

if (( BASH_VERSINFO[0] < 4 )); then
    for modern_bash in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        if [[ -x "$modern_bash" ]]; then
            exec "$modern_bash" "$0" "$@"
        fi
    done
    printf '[SKIP] requires Bash 4+\n'
    exit 0
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$ROOT/.." && pwd)"
LINUX_INSTALLER="$ROOT/install-core.sh"
MACOS_INSTALLER="${ODS_MACOS_INSTALLER_UNDER_TEST:-$ROOT/installers/macos/install-macos.sh}"
LINUX_PHASE06="${ODS_PHASE06_UNDER_TEST:-$ROOT/installers/phases/06-directories.sh}"
WINDOWS_PHASE06="$ROOT/installers/windows/phases/06-directories.ps1"
WINDOWS_PORTAL_SETUP="$ROOT/installers/windows/lib/wsl-portal-setup.ps1"
WINDOWS_INSTALLER="$ROOT/installers/windows/install-windows.ps1"
WINDOWS_CLI="$ROOT/installers/windows/ods.ps1"
ODS_CLI="$ROOT/ods-cli"
MACOS_CLI="$ROOT/installers/macos/ods-macos.sh"
MANIFEST="$ROOT/installers/lib/retired-openclaw-config.sha256"
NOTICE='The legacy OpenClaw extension was removed;'
AGENTS='Portal (Pixel) and Hermes are the supported agents.'

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }

# ── Flags: Linux and macOS parse them as notices and keep parsing ────────────
argument_loop() {
    sed -n '/^while \[\[ \$# -gt 0 \]\]; do$/,/^done$/p' "$1"
}

check_flags() {
    local label="$1" loop="$2" flag
    [[ -n "$loop" ]] || fail "$label argument loop not found"
    for flag in --openclaw --no-openclaw; do
        # --voice after the legacy flag proves parsing continued normally.
        # errexit is off inside an `if` condition, so each check exits itself.
        # shellcheck disable=SC2034  # read by the eval'd loop
        if ! (
            set -- "$flag" --voice
            ENABLE_VOICE=false
            eval "$loop"
            [[ "$ENABLE_VOICE" == true ]] || exit 1
            [[ -z "${ENABLE_OPENCLAW+x}${OPENCLAW_EXPLICIT+x}" ]] || exit 1
        ) >"$tmp/flag.out" 2>"$tmp/flag.err"; then
            cat "$tmp/flag.out" "$tmp/flag.err" >&2
            fail "$label did not accept $flag as a no-op"
        fi
        grep -Fq -- "$NOTICE $flag is ignored. $AGENTS" "$tmp/flag.err" \
            || fail "$label did not print the removal notice for $flag"
        pass "$label accepts $flag and prints the removal notice"
    done
}

check_flags install-core.sh "$(argument_loop "$LINUX_INSTALLER")"
check_flags install-macos.sh "$(argument_loop "$MACOS_INSTALLER")"

for installer in "$LINUX_INSTALLER" "$MACOS_INSTALLER"; do
    if grep -Eq 'ENABLE_OPENCLAW|OPENCLAW_EXPLICIT' "$installer"; then
        fail "$(basename "$installer") must not select the removed extension"
    fi
done
pass "Linux and macOS installers no longer carry OpenClaw feature state"

# ── Flags: Windows keeps -OpenClaw accepted and only prints a notice ─────────
for script in "$REPO_ROOT/install.ps1" "$ROOT/installers/windows-portal.ps1" "$WINDOWS_INSTALLER"; do
    grep -Fq '[switch]$OpenClaw' "$script" \
        || fail "$(basename "$script") must keep accepting -OpenClaw"
done
for script in "$WINDOWS_PORTAL_SETUP" "$WINDOWS_INSTALLER"; do
    grep -Fq "$NOTICE -OpenClaw is ignored. $AGENTS" "$script" \
        || fail "$(basename "$script") must print the removal notice for -OpenClaw"
done
if grep -Fq -- '--no-openclaw' "$WINDOWS_PORTAL_SETUP"; then
    fail "Portal setup must not pass a removed flag to the Linux installer"
fi
if grep -Eq 'openClawFlag|enableOpenClaw|EnableOpenClaw' "$WINDOWS_INSTALLER" \
        "$ROOT"/installers/windows/phases/*.ps1 "$ROOT"/installers/windows/lib/*.ps1; then
    fail "the Windows installer must not carry OpenClaw feature state"
fi
pass "Windows entry points accept -OpenClaw as a notice-only switch"

# ── The shipped template manifest ────────────────────────────────────────────
# The last shipped version of each template. Every older release version is
# listed too; these four cover the installs most owners have.
for entry in \
    "4f392ac433a29fdc0ff4955f7e93c2539d778e326344dd519049fd4a89b3488a  inject-token.js" \
    "fb3563bc70cfb2a902f1f302448894d2bfd17e8ad978c5cabbf399f2db54d999  openclaw.json" \
    "fb3563bc70cfb2a902f1f302448894d2bfd17e8ad978c5cabbf399f2db54d999  pro.json" \
    "c4366ad7d262e4675875e95ec441dad2feb77399079cb964e8827166ee64febd  openclaw-strix-halo.json"; do
    grep -Fxq "$entry" "$MANIFEST" || fail "template manifest is missing: $entry"
done
if grep -v '^#' "$MANIFEST" | grep -Evq '^[0-9a-f]{64}  [A-Za-z0-9._/-]+$'; then
    fail "template manifest has a malformed line"
fi
if grep -v '^#' "$MANIFEST" | grep -Fq '..'; then
    fail "template manifest must not name a path outside config/openclaw"
fi
# The installers refuse a linked parent folder; that check covers one level.
if grep -v '^#' "$MANIFEST" | grep -Eq '/[^ ]*/'; then
    fail "template manifest paths may be at most one folder deep"
fi
pass "the template manifest lists the shipped OpenClaw templates"

# ── Upgrade prune: Linux and macOS ───────────────────────────────────────────
# A throwaway source tree whose manifest pins the fixture templates below.
source_tree="$tmp/source"
mkdir -p "$source_tree/installers/lib"
printf 'shipped openclaw.json\n' > "$tmp/pristine-openclaw.json"
printf 'shipped inject-token.js\n' > "$tmp/pristine-inject-token.js"
printf 'shipped workspace notes\n' > "$tmp/pristine-SYSTEM.md"
{
    printf '# fixture manifest\n'
    printf '%s  openclaw.json\n' "$(sha256sum "$tmp/pristine-openclaw.json" | cut -d ' ' -f 1)"
    printf '%s  inject-token.js\n' "$(sha256sum "$tmp/pristine-inject-token.js" | cut -d ' ' -f 1)"
    printf '%s  workspace/SYSTEM.md\n' "$(sha256sum "$tmp/pristine-SYSTEM.md" | cut -d ' ' -f 1)"
    printf '%s  ../escape.json\n' "$(sha256sum "$tmp/pristine-openclaw.json" | cut -d ' ' -f 1)"
} > "$source_tree/installers/lib/retired-openclaw-config.sha256"

prune_code() {
    case "$1" in
        linux)
            awk '
                /# Retired bundled services\./ {grab=1}
                /# A Pixel-to-Hermes rerun must retire/ {exit}
                grab {print}
            ' "$LINUX_PHASE06"
            # Phase 06 defines the prune as a function and calls it later.
            printf '%s\n' 'declare -F _phase06_prune_retired_services >/dev/null \' \
                '    || { echo "FAIL: phase 06 must define _phase06_prune_retired_services" >&2; exit 3; }' \
                '_phase06_prune_retired_services'
            ;;
        macos)
            awk '
                index($0, "extensions/services/odsforge\" ]]; then") {grab=1}
                grab && /# Copy extensions library to data dir/ {exit}
                grab {print}
            ' "$MACOS_INSTALLER"
            ;;
    esac
}

run_prune() {
    local platform="$1" install="$2" home="$3" source="${4:-$source_tree}" code
    code="$(prune_code "$platform")"
    [[ -n "$code" ]] || fail "$platform prune code not found"
    (
        INSTALL_DIR="$install"
        SCRIPT_DIR="$source"
        SOURCE_ROOT="$source"
        HOME="$home"
        _phase06_step() { :; }
        log() { printf 'log: %s\n' "$*"; }
        ai() { printf 'ai: %s\n' "$*"; }
        eval "$code"
    )
}

check_prune() {
    local platform="$1" install home out
    # Upgrade with owner data: untouched templates go, everything else stays.
    install="$tmp/$platform-used"
    home="$tmp/$platform-used-home"
    mkdir -p "$install/extensions/services/openclaw" "$install/extensions/services/hermes" \
        "$install/data/openclaw/home" "$install/config/openclaw/workspace" "$home"
    printf 'services: {}\n' > "$install/extensions/services/openclaw/compose.yaml.disabled"
    printf 'services: {}\n' > "$install/extensions/services/hermes/compose.yaml"
    printf '{}\n' > "$install/data/openclaw/home/openclaw.json"
    # The installer wrote the owner's model into openclaw.json; pro.json is a
    # file the manifest does not list.
    printf 'owner changed this template\n' > "$install/config/openclaw/openclaw.json"
    cp "$tmp/pristine-inject-token.js" "$install/config/openclaw/inject-token.js"
    printf 'owner file\n' > "$install/config/openclaw/pro.json"
    printf 'notes\n' > "$install/config/openclaw/workspace/MEMORY.md"
    cp "$tmp/pristine-openclaw.json" "$install/config/escape.json"
    out="$tmp/$platform-used.out"
    run_prune "$platform" "$install" "$home" > "$out"
    [[ ! -e "$install/extensions/services/openclaw" ]] \
        || fail "$platform upgrade kept the stale OpenClaw service tree"
    [[ -f "$install/extensions/services/hermes/compose.yaml" ]] \
        || fail "$platform upgrade touched a supported service"
    [[ ! -e "$install/config/openclaw/inject-token.js" ]] \
        || fail "$platform upgrade kept an untouched shipped template"
    grep -Fxq 'owner changed this template' "$install/config/openclaw/openclaw.json" \
        || fail "$platform upgrade deleted a shipped template the owner changed"
    [[ -f "$install/config/openclaw/pro.json" && -f "$install/config/openclaw/workspace/MEMORY.md" ]] \
        || fail "$platform upgrade deleted a file the owner added or workspace data"
    [[ -f "$install/data/openclaw/home/openclaw.json" ]] \
        || fail "$platform upgrade deleted the owner's OpenClaw data"
    [[ -f "$install/config/escape.json" ]] \
        || fail "$platform upgrade followed a manifest path outside config/openclaw"
    grep -Fq 'files in config/openclaw and data/openclaw were kept; delete them by hand' "$out" \
        || fail "$platform upgrade did not name the folders it kept"
    pass "$platform upgrade removes the service tree and untouched templates and keeps owner data"

    # Upgrade of an install that never used OpenClaw: nothing is left behind.
    install="$tmp/$platform-unused"
    home="$tmp/$platform-unused-home"
    mkdir -p "$install/extensions/services/openclaw" "$install/config/openclaw/workspace" "$home"
    printf 'services: {}\n' > "$install/extensions/services/openclaw/compose.yaml.disabled"
    cp "$tmp/pristine-openclaw.json" "$install/config/openclaw/openclaw.json"
    cp "$tmp/pristine-inject-token.js" "$install/config/openclaw/inject-token.js"
    cp "$tmp/pristine-SYSTEM.md" "$install/config/openclaw/workspace/SYSTEM.md"
    out="$tmp/$platform-unused.out"
    run_prune "$platform" "$install" "$home" > "$out"
    [[ ! -e "$install/config/openclaw" && ! -e "$install/extensions/services/openclaw" ]] \
        || fail "$platform upgrade left the unused OpenClaw templates behind"
    if grep -Fq 'delete them by hand' "$out"; then
        fail "$platform upgrade warned about OpenClaw files an owner never had"
    fi
    pass "$platform upgrade of an install that never used OpenClaw leaves nothing and stays quiet"

    # A linked folder is never followed, even to a byte-identical template.
    install="$tmp/$platform-linked"
    home="$tmp/$platform-linked-home"
    mkdir -p "$install/config/openclaw" "$tmp/$platform-outside" "$home"
    cp "$tmp/pristine-SYSTEM.md" "$tmp/$platform-outside/SYSTEM.md"
    ln -s "$tmp/$platform-outside" "$install/config/openclaw/workspace"
    run_prune "$platform" "$install" "$home" > "$tmp/$platform-linked.out"
    [[ -f "$tmp/$platform-outside/SYSTEM.md" ]] \
        || fail "$platform upgrade followed a linked folder out of config/openclaw"
    pass "$platform upgrade never follows a linked folder out of config/openclaw"

    # Fresh install: nothing to prune, nothing printed, nothing created.
    install="$tmp/$platform-fresh"
    home="$tmp/$platform-fresh-home"
    mkdir -p "$install/extensions/services/hermes" "$home"
    out="$tmp/$platform-fresh.out"
    run_prune "$platform" "$install" "$home" > "$out"
    if grep -qi 'openclaw' "$out"; then
        fail "$platform fresh install mentions the removed extension"
    fi
    [[ ! -e "$install/data/openclaw" && ! -e "$install/config/openclaw" ]] \
        || fail "$platform fresh install created OpenClaw folders"
    pass "$platform fresh install stays silent and creates no OpenClaw folders"
}

check_prune linux
check_prune macos

# A source tree copied from a Windows checkout carries a CRLF manifest.
crlf_source="$tmp/source-crlf"
mkdir -p "$crlf_source/installers/lib"
awk '{printf "%s\r\n", $0}' "$source_tree/installers/lib/retired-openclaw-config.sha256" \
    > "$crlf_source/installers/lib/retired-openclaw-config.sha256"
for platform in linux macos; do
    install="$tmp/$platform-crlf"
    home="$tmp/$platform-crlf-home"
    mkdir -p "$install/config/openclaw/workspace" "$home"
    cp "$tmp/pristine-openclaw.json" "$install/config/openclaw/openclaw.json"
    cp "$tmp/pristine-SYSTEM.md" "$install/config/openclaw/workspace/SYSTEM.md"
    run_prune "$platform" "$install" "$home" "$crlf_source" > "$tmp/$platform-crlf.out"
    [[ ! -e "$install/config/openclaw" ]] \
        || fail "$platform upgrade did not match templates against a CRLF manifest"
done
pass "Linux and macOS match templates against a manifest with CRLF lines"

# AMD installs may still run memory-shepherd timers against the workspace.
install="$tmp/linux-timers"
home="$tmp/linux-timers-home"
mkdir -p "$install/config/openclaw/workspace" "$home/.config/systemd/user"
printf 'notes\n' > "$install/config/openclaw/workspace/MEMORY.md"
printf '[Unit]\n' > "$home/.config/systemd/user/memory-shepherd-workspace.timer"
run_prune linux "$install" "$home" > "$tmp/linux-timers.out"
grep -Fq 'Disable the memory-shepherd-memory and memory-shepherd-workspace user timers' "$tmp/linux-timers.out" \
    || fail "Linux notice must point out memory-shepherd timers that still use the workspace"
pass "Linux notice points out memory-shepherd timers that still use the workspace"

# ── Linux: the prune waits for an unfinished Pixel source plan ───────────────
# The held Pixel source transaction records the installed extensions tree as
# its baseline and checks it again when it finishes, resumes or rolls back. Run
# the real phase 06 block with the Pixel helpers stubbed and record whether the
# retired service tree still existed at each transaction step.
pixel_block="$(awk '
    /# Retired bundled services\./ {grab=1}
    grab {print}
    grab && /^    unset _phase06_pixel_marker / {exit}
' "$LINUX_PHASE06")"
[[ -n "$pixel_block" ]] || fail "could not extract the Linux prune and Pixel source block"

run_pixel_case() {
    local name="$1" pixel_runtime="$2" marker="$3" transition="$4" status="$5"
    local install="$tmp/pixel-$name" home="$tmp/pixel-$name-home"
    mkdir -p "$install/extensions/services/openclaw" "$home/.config/ods"
    printf 'services: {}\n' > "$install/extensions/services/openclaw/compose.yaml.disabled"
    if [[ "$marker" == true ]]; then
        printf '{}\n' > "$home/.config/ods/pixel-managed.json"
    fi
    : > "$tmp/pixel-$name.calls"
    (
        INSTALL_DIR="$install"
        SCRIPT_DIR="$source_tree"
        HOME="$home"
        ENABLE_PIXEL_RUNTIME="$pixel_runtime"
        _phase06_requested_pixel_ref=0000000000000000000000000000000000000000
        calls="$tmp/pixel-$name.calls"
        tree_state() {
            if [[ -d "$INSTALL_DIR/extensions/services/openclaw" ]]; then echo present; else echo absent; fi
        }
        _phase06_step() { :; }
        log() { :; }
        ai() { :; }
        ai_warn() { :; }
        error() { printf 'error: %s\n' "$*" >> "$calls"; }
        ods_pixel_install_owner() { echo owner; }
        ods_pixel_owner_home() { echo "$HOME"; }
        _ods_pixel_source_transition_required() { return "$transition"; }
        ods_sudo() { [[ "$*" == "test -d /var/lib/ods-pixel-access/source-upgrade" ]]; }
        _ods_pixel_restore_transition_source() { :; }
        _ods_pixel_openclaw_bin() { echo /fixture/openclaw; }
        _ods_pixel_install_access_service() { :; }
        ods_pixel_uninstall_managed() { echo "deactivate $(tree_state)" >> "$calls"; }
        _ods_pixel_source_upgrade() {
            echo "$1 $(tree_state)" >> "$calls"
            case "$1" in
                status) printf '%s\n' "$status" ;;
                hold) printf '%064d\n' 0 ;;
            esac
        }
        run() { eval "$pixel_block"; }
        run
        echo "end $(tree_state)" >> "$calls"
    ) >/dev/null
}

expect_calls() {
    local name="$1" expected="$2"
    if [[ "$(cat "$tmp/pixel-$name.calls")" != "$expected" ]]; then
        printf 'expected:\n%s\nactual:\n%s\n' "$expected" "$(cat "$tmp/pixel-$name.calls")" >&2
        fail "Pixel source plan case '$name' ran the prune at the wrong point"
    fi
}

# An unfinished plan staged by an earlier release keeps the tree it recorded;
# `stage` then resumes or refuses it without a prune in between.
run_pixel_case held true true 0 '{"pending":true,"phase":"held","transaction":"fixture"}'
expect_calls held "$(printf '%s\n' 'status present' 'stage present' 'status present' \
    'hold present' 'copy present' 'downstream present' 'end present')"
pass "Linux keeps the retired tree while an unfinished Pixel source plan is pending"

# A complete plan is finished against the tree it recorded, then pruned before
# the new plan is staged.
run_pixel_case complete true true 0 '{"pending":true,"phase":"complete"}'
expect_calls complete "$(printf '%s\n' 'status present' 'finish present' 'stage absent' \
    'status absent' 'hold absent' 'copy absent' 'downstream absent' 'end absent')"
pass "Linux finishes a complete Pixel source plan before it prunes and stages a new one"

run_pixel_case idle true true 0 '{"pending":false}'
expect_calls idle "$(printf '%s\n' 'status present' 'stage absent' 'status absent' \
    'hold absent' 'copy absent' 'downstream absent' 'end absent')"
pass "Linux prunes before staging when no Pixel source plan is pending"

run_pixel_case current true true 1 '{"pending":false}'
expect_calls current "$(printf '%s\n' 'status present' 'end absent')"
pass "Linux prunes when the Pixel source needs no transition"

run_pixel_case deactivate false true 0 '{"pending":false}'
expect_calls deactivate "$(printf '%s\n' 'deactivate present' 'end absent')"
pass "Linux prunes after deactivating Pixel"

run_pixel_case plain true false 0 '{"pending":false}'
expect_calls plain 'end absent'
pass "Linux prunes on installs without a managed Pixel runtime"

# ── Upgrade prune: Windows mirrors the Linux behaviour ───────────────────────
grep -Fq '$_retiredOpenClaw = Join-Path $installDir "extensions\services\openclaw"' "$WINDOWS_PHASE06" \
    || fail "Windows phase 06 must locate the stale OpenClaw service tree"
grep -Fq 'Remove-Item -LiteralPath $_retiredOpenClaw -Recurse -Force' "$WINDOWS_PHASE06" \
    || fail "Windows phase 06 must remove the stale OpenClaw service tree"
grep -Fq 'installers\lib\retired-openclaw-config.sha256' "$WINDOWS_PHASE06" \
    || fail "Windows phase 06 must read the shipped template manifest"
grep -Fq 'Get-FileHash -LiteralPath $_template -Algorithm SHA256' "$WINDOWS_PHASE06" \
    || fail "Windows phase 06 must compare templates by SHA-256"
grep -Fq 'Its remaining files in $($_openClawKept -join' "$WINDOWS_PHASE06" \
    || fail "Windows phase 06 must name the folders it kept"
if grep -Eiq 'Remove-Item[^#]*(data|config)[\\/]openclaw' "$WINDOWS_PHASE06"; then
    fail "Windows phase 06 must not delete data\\openclaw or config\\openclaw outright"
fi
pass "Windows phase 06 removes the service tree and untouched templates and keeps owner data"

# ── `ods start` points out an orphaned ods-openclaw container ────────────────
warn_function="$(sed -n '/^_ods_cli_warn_legacy_openclaw_container() {$/,/^}$/p' "$ODS_CLI")"
[[ -n "$warn_function" ]] || fail "ods-cli must define the legacy container warning"
for state in present absent; do
    out="$(
        docker() {
            [[ "$*" == "container inspect ods-openclaw" ]] || return 2
            [[ "$state" == present ]]
        }
        warn() { printf 'warn: %s\n' "$*"; }
        eval "$warn_function"
        _ods_cli_warn_legacy_openclaw_container
    )"
    if [[ "$state" == present ]]; then
        grep -Fq 'docker rm -f ods-openclaw' <<<"$out" \
            || fail "ods start must say how to remove a remaining ods-openclaw container"
    elif [[ -n "$out" ]]; then
        fail "ods start must stay quiet without an ods-openclaw container"
    fi
done
start_function="$(sed -n '/^cmd_start() {$/,/^}$/p' "$ODS_CLI")"
grep -Fq '_ods_cli_warn_legacy_openclaw_container' <<<"$start_function" \
    || fail "ods start must check for a remaining ods-openclaw container"
macos_start_function="$(sed -n '/^cmd_start() {$/,/^}$/p' "$MACOS_CLI")"
grep -Fq 'docker container inspect ods-openclaw' <<<"$macos_start_function" \
    && grep -Fq 'docker rm -f ods-openclaw' <<<"$macos_start_function" \
    || fail "ods-macos.sh start must check for a remaining ods-openclaw container"
grep -Fq 'Test-ODSLegacyOpenClawContainer' "$WINDOWS_CLI" \
    || fail "ods.ps1 start must check for a remaining ods-openclaw container"
pass "ods start warns while a legacy ods-openclaw container remains"

echo "All legacy OpenClaw removal checks passed."
