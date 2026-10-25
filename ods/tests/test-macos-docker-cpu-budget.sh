#!/usr/bin/env bash
# An 8-core Mac must be able to install voice and the Full Stack (#3600).
#
# install-macos.sh used to demand 10 Docker CPUs whenever voice was selected,
# to fit Kokoro's old fixed 8-CPU compose pin, so an 8-core MacBook Air or Pro
# could never install it. The env generator now caps every service CPU limit
# (TTS, Whisper, Hermes) to Docker's CPUs, so voice shares the base floor. The
# floor's refusal names ODS_MIN_DOCKER_CPUS and a recovery step, and the
# installer's EXIT trap adds the Discord help link.
#
# This runs the installer's own CPU check functions and the two top-level steps
# that call them (preflight, and after feature selection) with Docker's CPU
# count stubbed.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLER="$ROOT_DIR/installers/macos/install-macos.sh"

PASS=0
FAIL=0
pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }
has() { printf '%s\n' "$1" | grep -qF -- "$2"; }

ODS_HELP_DISCORD_URL="$(sed -n 's/^ODS_HELP_DISCORD_URL="\(.*\)"$/\1/p' "$ROOT_DIR/installers/macos/lib/constants.sh")"
ODS_LOG_FILE=/dev/null
[[ -n "$ODS_HELP_DISCORD_URL" ]] || { echo "[FAIL] ODS_HELP_DISCORD_URL not found"; exit 1; }

eval "$(sed -n '/^_require_docker_cpu_budget() {/,/^}/p' "$INSTALLER")"
eval "$(sed -n '/^_warn_voice_cpu_budget() {/,/^}/p' "$INSTALLER")"
eval "$(sed -n '/^_macos_help_on_failure() {/,/^}/p' "$INSTALLER")"
PREFLIGHT_STEP="$(sed -n "/^# Pre-flight the docker daemon's CPU allocation/,/^# Catch a common Colima/p" "$INSTALLER" | sed '/^#/d')"
FEATURE_STEP="$(sed -n '/^info_box "  ComfyUI:"/,/^# PHASE 4 -- SETUP/p' "$INSTALLER" | sed '1d;/^#/d')"
if ! declare -F _require_docker_cpu_budget >/dev/null || ! declare -F _macos_help_on_failure >/dev/null \
    || [[ -z "${PREFLIGHT_STEP//[[:space:]]/}" || -z "${FEATURE_STEP//[[:space:]]/}" ]]; then
    echo "[FAIL] the Docker CPU checks were not found in $INSTALLER"
    exit 1
fi

# Host facts and installer UI.
get_docker_available_cpus() { echo "$FAKE_DOCKER_CPUS"; }
_active_colima_hint_args() { :; }
ai() { printf '%s\n' "$1"; }
ai_ok() { printf 'OK: %s\n' "$1"; }
ai_warn() { printf 'WARN: %s\n' "$1"; }
ai_err() { printf 'ERROR: %s\n' "$1"; }

OUT=""
RC=0
run_steps() {
    # $1 = Docker CPUs, $2 = voice selected (true|false), $3 = ODS_MIN_DOCKER_CPUS
    # ("" leaves it unset), $4 = Docker backend. Runs both steps the way the
    # installer does, with its EXIT trap, in a subshell that may exit.
    RC=0
    OUT="$(
        exec 2>&1
        FAKE_DOCKER_CPUS="$1"
        ENABLE_VOICE="$2"
        DOCKER_BACKEND="${4:-desktop}"
        if [[ -n "$3" ]]; then
            ODS_MIN_DOCKER_CPUS="$3"
        else
            unset ODS_MIN_DOCKER_CPUS
        fi
        trap _macos_help_on_failure EXIT
        eval "$PREFLIGHT_STEP"
        eval "$FEATURE_STEP"
        echo "STEPS COMPLETED"
    )" || RC=$?
}

# --- The reported Mac: 8-core MacBook Air, Docker Desktop at 8 CPUs ----------
run_steps 8 true "" desktop
if [[ "$RC" -eq 0 ]] && has "$OUT" "STEPS COMPLETED"; then
    pass "8 Docker CPUs: voice / Full Stack is allowed"
else
    fail "8 Docker CPUs: voice / Full Stack must be allowed (exit $RC): $OUT"
fi
if has "$OUT" "WARN:" || has "$OUT" "ERROR:"; then
    fail "8 Docker CPUs: voice needs no warning, its services get their full CPU budgets: $OUT"
else
    pass "8 Docker CPUs: no warning for voice"
fi

run_steps 6 true "" desktop
if [[ "$RC" -eq 0 ]] && has "$OUT" "STEPS COMPLETED" && ! has "$OUT" "WARN:"; then
    pass "6 Docker CPUs (the default floor): voice is allowed without a warning"
else
    fail "6 Docker CPUs: voice must be allowed without a warning (exit $RC): $OUT"
fi

run_steps 8 false "" desktop
if [[ "$RC" -eq 0 ]] && has "$OUT" "STEPS COMPLETED" && ! has "$OUT" "WARN:"; then
    pass "8 Docker CPUs without voice: allowed"
else
    fail "8 Docker CPUs without voice must be allowed (exit $RC): $OUT"
fi

# --- Below the floor: a refusal with a way forward ----------------------------
run_steps 4 false "" desktop
if [[ "$RC" -ne 0 ]] && ! has "$OUT" "STEPS COMPLETED"; then
    pass "4 Docker CPUs: below the default floor of 6, the install stops"
else
    fail "4 Docker CPUs: the install must stop below the floor (exit $RC): $OUT"
fi
if has "$OUT" "ODS_MIN_DOCKER_CPUS=4"; then
    pass "the refusal names ODS_MIN_DOCKER_CPUS with the value that would proceed"
else
    fail "the refusal must name ODS_MIN_DOCKER_CPUS=4: $OUT"
fi
if has "$OUT" "Docker Desktop -> Settings -> Resources -> Advanced and raise CPUs to 6+"; then
    pass "the refusal says how to give Docker Desktop more CPUs"
else
    fail "the refusal must say how to give Docker Desktop more CPUs: $OUT"
fi
if has "$OUT" "Need help? Ask on the ODS Discord: $ODS_HELP_DISCORD_URL"; then
    pass "the refusal ends with the Discord help link"
else
    fail "the refusal must end with the Discord help link: $OUT"
fi
if has "$OUT" "range of CPUs"; then
    fail "the refusal must not blame a compose CPU-range failure that the capped limits rule out: $OUT"
else
    pass "the refusal does not claim a compose CPU-range failure"
fi

run_steps 4 true "" colima
if [[ "$RC" -ne 0 ]] && has "$OUT" "--cpu 6 --memory 12 --disk 60" && has "$OUT" "ODS_MIN_DOCKER_CPUS=4"; then
    pass "Colima at 4 CPUs: the refusal gives the colima command and the override"
else
    fail "Colima at 4 CPUs: the refusal must give the colima command and the override (exit $RC): $OUT"
fi

# --- ODS_MIN_DOCKER_CPUS lowers the floor ------------------------------------
run_steps 4 true 4 desktop
if [[ "$RC" -eq 0 ]] && has "$OUT" "STEPS COMPLETED" && ! has "$OUT" "WARN:"; then
    pass "ODS_MIN_DOCKER_CPUS=4 with 4 Docker CPUs: voice is allowed, still at full speed"
else
    fail "ODS_MIN_DOCKER_CPUS=4 with 4 Docker CPUs: voice must be allowed without a warning (exit $RC): $OUT"
fi

run_steps 2 true 2 desktop
if [[ "$RC" -eq 0 ]] && has "$OUT" "STEPS COMPLETED" && has "$OUT" "WARN: Voice: Docker has 2 CPU(s)"; then
    pass "ODS_MIN_DOCKER_CPUS=2 with 2 Docker CPUs: voice is allowed with a slower-speech warning"
else
    fail "ODS_MIN_DOCKER_CPUS=2 with 2 Docker CPUs: voice must be allowed with a warning (exit $RC): $OUT"
fi

run_steps 2 false 2 desktop
if [[ "$RC" -eq 0 ]] && ! has "$OUT" "WARN:"; then
    pass "ODS_MIN_DOCKER_CPUS=2 without voice: no voice warning"
else
    fail "ODS_MIN_DOCKER_CPUS=2 without voice must not warn about voice (exit $RC): $OUT"
fi

echo "Result: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
