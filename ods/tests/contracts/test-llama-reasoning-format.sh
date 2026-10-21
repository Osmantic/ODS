#!/usr/bin/env bash
# Contract: every native llama.cpp launcher pins --reasoning-format.
#
# LLAMA_REASONING is documented in .env.schema.json as "off (default) | auto |
# on. Off prevents thinking models from consuming the entire token budget on
# internal reasoning." llama.cpp has its own default, so a launcher that never
# passes --reasoning-format does not merely ignore the operator's setting -- it
# leaves reasoning at whatever the llama.cpp build prefers, which is the
# behaviour the default exists to prevent.
#
# Docker is exempt: docker-compose.base.yml passes LLAMA_ARG_REASONING into the
# container environment. The CPU image must support that setting; b8248 did
# not, so its documented default was silently ignored on CPU-only installs.
#
# The .env values (off/on/auto) are not llama.cpp's vocabulary, so each
# launcher also has to map them; the check below requires both the flag and the
# mapping input.
#
# Native macOS hands its mapped format to installers/macos/lib/
# native-checkpoint-args.py, which passes --reasoning instead on runtimes that
# have it (b9014, where --reasoning-format none leaked an empty think block into
# every reply). tests/test_macos_runtime_llama_args.py checks the final argv.
#
# Native Windows chooses through installers/windows/lib/native-llama-args.ps1
# (Get-ODSNativeReasoningArgs): --reasoning on runtimes that list it (b9014),
# --reasoning-format on older ones (b8248), plus --reasoning-budget 0 for off
# where the binary has it, which is what disables thinking on b8248. A legacy
# installation maps LLAMA_REASONING in native-llama-legacy.ps1
# (Get-ODSNativeLlamaLegacyTuning), and a registered model-store profile keeps
# --reasoning-format. The Portal task has no .env on Windows and pins off,
# Docker's default. New-ODSNativeLlamaLaunchArguments passes the choice on and
# Assert-ODSNativeLlamaOptions admits only those shapes. The host agent and
# scripts/bootstrap-upgrade.sh relaunch the Windows runtime through
# "ods.ps1 native-llm-restart". tests/test-windows-native-checkpoint-args.ps1
# checks the helper.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FAILURES=0

fail() {
    echo "[FAIL] $*" >&2
    FAILURES=$((FAILURES + 1))
}

pass() {
    echo "[PASS] $*"
}

# Native Windows is checked below.
native_launchers=(
    bin/ods-host-agent.py
    installers/macos/install-macos.sh
    installers/macos/ods-macos.sh
    scripts/bootstrap-upgrade.sh
)

for target in "${native_launchers[@]}"; do
    path="$ROOT_DIR/$target"
    if [[ ! -f "$path" ]]; then
        fail "$target is missing; update this contract if the launcher moved"
        continue
    fi
    if ! grep -q -- '--reasoning-format' "$path" && ! grep -q 'Get-ODSNativeReasoningArgs' "$path"; then
        fail "$target starts llama-server without --reasoning-format; LLAMA_REASONING is ignored and thinking mode falls back to the llama.cpp default"
        continue
    fi
    if ! grep -q 'LLAMA_REASONING' "$path"; then
        fail "$target passes --reasoning-format but never reads LLAMA_REASONING, so the documented setting has no effect"
        continue
    fi
    pass "$target pins --reasoning-format from LLAMA_REASONING"
done

# On b9014, --reasoning-format alone leaves --reasoning at auto, so every
# Windows launch path must pass --reasoning where the runtime has it. Each one
# builds its argv in installers/windows/lib (see above).
win_runtime=installers/windows/lib/native-llama-runtime.ps1
win_legacy=installers/windows/lib/native-llama-legacy.ps1
win_portal=installers/windows/lib/wsl-portal-amd.ps1

# A top-level PowerShell function: its "function <Name>" line through the
# closing brace in column 0, the layout of the Windows sources.
ps_function() {
    awk -v name="$2" '
        $0 ~ ("^function " name "[ ({]") { inside = 1 }
        inside { print }
        inside && /^}/ { exit }
    ' "$ROOT_DIR/$1"
}

check_windows() {
    local file="$1" function="$2" needle="$3" label="$4" body
    if [[ ! -f "$ROOT_DIR/$file" ]]; then
        fail "$file is missing; update this contract if the Windows launch moved"
        return
    fi
    body="$(ps_function "$file" "$function")"
    if grep -Fq -- "$needle" <<<"$body"; then
        pass "$function in $file: $label"
    else
        fail "$function in $file lacks $needle, so this no longer holds: $label"
    fi
}

check_windows "$win_legacy" Get-ODSNativeLlamaLegacyReasoning "'LLAMA_REASONING'" \
    "a legacy installation reads LLAMA_REASONING from .env"
check_windows "$win_legacy" Get-ODSNativeLlamaLegacyReasoning "\$format = 'none'" \
    "off maps to --reasoning-format none for runtimes without --reasoning"
check_windows "$win_legacy" Get-ODSNativeLlamaLegacyTuning \
    'Get-ODSNativeReasoningArgs -Executable $Executable -Mode $mode -FallbackFormat $format' \
    "the legacy pinned launch passes --reasoning on runtimes that have it"
check_windows "$win_legacy" New-ODSNativeLlamaLegacyLaunch 'ReasoningArguments = $tuning.ReasoningArguments' \
    "the legacy pinned launch carries the .env reasoning choice"
check_windows "$win_legacy" New-ODSNativeLlamaLegacyLaunch "'--reasoning-format', \$reasoningFormat" \
    "a registered model-store profile pins --reasoning-format from LLAMA_REASONING"
check_windows "$win_portal" New-ODSPortalRuntimeOptions \
    "Get-ODSNativeReasoningArgs -Executable \$Runtime.ExecutablePath -Mode 'off'" \
    "the Portal task pins thinking off, as Docker's default does"
check_windows "$win_runtime" New-ODSNativeLlamaLaunchArguments '$Options.ReasoningArguments' \
    "the pinned Windows launch passes the reasoning choice to llama-server"
for flag in "'--reasoning'" "'--reasoning-format'" "'--reasoning-budget'"; do
    check_windows "$win_runtime" Assert-ODSNativeLlamaOptions "$flag" \
        "saved launch options admit the $flag form Get-ODSNativeReasoningArgs returns"
done

# Relaunches after a model change go through ods.ps1, which builds no flags of
# its own (above); neither the upgrade script nor the host agent does either.
bootstrap_restart=""
if [[ -f "$ROOT_DIR/scripts/bootstrap-upgrade.sh" ]]; then
    bootstrap_restart="$(sed -n '/^restart_windows_native_llama_server_with_full_model()/,/^}/p' "$ROOT_DIR/scripts/bootstrap-upgrade.sh")"
fi
if grep -Fq 'native-llm-restart "$install_dir_win"' <<<"$bootstrap_restart" \
    && ! grep -q -- '--reasoning' <<<"$bootstrap_restart"; then
    pass "scripts/bootstrap-upgrade.sh relaunches Windows runtimes through ods.ps1, which passes --reasoning where it exists"
else
    fail "scripts/bootstrap-upgrade.sh must relaunch Windows runtimes through ods.ps1 native-llm-restart, not build their flags"
fi
if grep -q '"native-llm-restart"' "$ROOT_DIR/bin/ods-host-agent.py"; then
    pass "bin/ods-host-agent.py relaunches Windows runtimes through ods.ps1, which passes --reasoning where it exists"
else
    fail "bin/ods-host-agent.py must relaunch Windows runtimes through ods.ps1 native-llm-restart, not build their flags"
fi

# Docker's route is the container environment rather than an argv flag.
if grep -q 'LLAMA_ARG_REASONING=${LLAMA_REASONING' "$ROOT_DIR/docker-compose.base.yml"; then
    pass "docker-compose.base.yml forwards LLAMA_REASONING to the container environment"
else
    fail "docker-compose.base.yml no longer forwards LLAMA_REASONING as LLAMA_ARG_REASONING"
fi

cpu_image="$(sed -n 's/^[[:space:]]*image: \${LLAMA_SERVER_IMAGE:-\(.*\)}[[:space:]]*$/\1/p' "$ROOT_DIR/docker-compose.cpu.yml" | head -n 1)"
if [[ "$cpu_image" == ghcr.io/ggml-org/llama.cpp:server-b9014@sha256:* ]]; then
    pass "docker-compose.cpu.yml defaults to a llama.cpp build that honors LLAMA_ARG_REASONING"
else
    fail "docker-compose.cpu.yml must use a reasoning-aware llama.cpp build by default (got '${cpu_image}')"
fi

# The CPU installer pull and AMD-to-CPU fallback must agree with Compose,
# digest included. Otherwise a fresh install pulls the wrong image, or the
# fallback writes an old tag to .env and overrides Compose's default.
for target in installers/phases/08-images.sh installers/phases/11-services.sh; do
    if [[ -n "$cpu_image" ]] && grep -Fq "$cpu_image" "$ROOT_DIR/$target" \
        && ! grep -Fq 'ghcr.io/ggml-org/llama.cpp:server-b8248' "$ROOT_DIR/$target"; then
        pass "$target uses the reasoning-aware CPU image by default"
    else
        fail "$target must default to the same reasoning-aware CPU image as Compose"
    fi
done

if (( FAILURES > 0 )); then
    echo ""
    echo "[FAIL] llama.cpp reasoning-format contract"
    exit 1
fi

echo ""
echo "[PASS] every llama.cpp launch path applies LLAMA_REASONING"
