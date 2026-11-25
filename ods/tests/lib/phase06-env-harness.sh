#!/usr/bin/env bash
# ============================================================================
# ODS Tests -- render phase 06's .env without installing anything
# ============================================================================
# ods_phase06_render_env PHASE06 ODS_ROOT INSTALL_DIR FIXTURE
#
# Runs PHASE06's .env generation, from its "generate-env" step through the
# lines after the template (up to the chmod), in a fresh bash. The shipped
# helper libraries come from ODS_ROOT; host probes are stubbed (no Docker,
# fixed secrets, clock, host name, CPUs and IDs), so two phase 06 versions
# given the same FIXTURE write comparable bytes. FIXTURE is a file of shell
# assignments describing the route (GPU_BACKEND, TIER, LLM_MODEL, ...). An
# existing INSTALL_DIR/.env is read as the installation's previous values.
# Leaves INSTALL_DIR/.env and, in INSTALL_DIR/exported-llm-backend, the
# LLM_BACKEND it exports to later phases; stderr goes to
# INSTALL_DIR/render.err. Returns the render's status.
# ============================================================================

ods_phase06_render_env() {
    local phase06="$1" root="$2" install_dir="$3" fixture="$4" script
    local segment readers secrets
    segment="$(awk '
        /^    # ── \.env merge logic: preserve user-configured values on re-install ──$/ { emit = 1 }
        /^    chmod 600 "\$INSTALL_DIR\/\.env"  # Secure secrets file$/ { exit }
        emit { print }
    ' "$phase06")"
    readers="$(awk '
        /^    _env_get\(\) \{$/ { emit = 1 }
        emit { print }
        emit && /^    _env_get_explicit_first\(\) \{$/ { last = 1 }
        last && /^    }$/ { exit }
    ' "$phase06")"
    secrets="$(awk '
        /^_phase06_generate_hex_secret\(\) \{$/ || /^_phase06_env_hex_secret\(\) \{$/ { emit = 1 }
        emit { print }
        emit && /^}$/ { emit = 0 }
    ' "$phase06")"
    [[ -n "$segment" && -n "$readers" && -n "$secrets" ]] || {
        printf 'phase 06 .env generation not found in %s\n' "$phase06" >&2
        return 2
    }
    mkdir -p "$install_dir/config/litellm"
    script="$install_dir/render.sh"
    {
        printf '%s\n' 'set -euo pipefail'
        printf 'SCRIPT_DIR=%q\nINSTALL_DIR=%q\nLOG_FILE=%q\n' "$root" "$install_dir" "$install_dir/render.log"
        cat <<'PRELUDE'
for _lib in lib/safe-env.sh lib/dotenv-quote.sh installers/lib/native-llm.sh installers/lib/amd-runtime.sh \
    installers/lib/tier-map.sh installers/lib/llama-memory-budget.sh installers/lib/external-services.sh; do
    # shellcheck source=/dev/null
    source "$SCRIPT_DIR/$_lib"
done
unset _lib
ods_progress() { :; }
_phase06_step() { :; }
log() { :; }
ai() { :; }
ai_ok() { :; }
ai_warn() { printf 'WARN %s\n' "$*" >&2; }
warn() { printf 'WARN %s\n' "$*" >&2; }
error() { printf 'ERROR %s\n' "$*" >&2; exit 1; }
# Never reach a Docker daemon from a test.
docker() { printf 'the .env harness must not run docker\n' >&2; exit 97; }
ods_docker_memory_gb() { return 1; }
calculate_llama_cpu_budget() { printf '16 2 32\n'; }
ods_physical_cpu_cores() { printf '16\n'; }
openssl() {
    case "$2" in
        -hex) printf '%0*d\n' "$(($3 * 2))" 7 ;;
        *) printf 'Zml4ZWQtc2VjcmV0LXZhbHVl\n' ;;
    esac
}
date() { printf '2026-10-27T00:00:00Z\n'; }
hostname() { printf 'ods-fixture\n'; }
id() { printf '1000\n'; }
_phase06_compose_uid=1000
_phase06_compose_gid=1000
VERSION=3.0.0-fixture
TIER_NAME=Fixture
SYSTEM_TZ=UTC
ENABLE_PIXEL_RUNTIME=false
_env_existing=""
[[ -f "$INSTALL_DIR/.env" ]] && _env_existing="$INSTALL_DIR/.env"
PRELUDE
        printf '%s\n' "$secrets" "$readers"
        cat "$fixture"
        printf '%s\n' "$segment"
        # The previous .env snapshot is carried after the chmod, outside this render.
        printf '%s\n' '[[ -z "${_phase06_previous_env:-}" ]] || rm -f -- "$_phase06_previous_env"'
        # What later phases (and Compose) see exported.
        # shellcheck disable=SC2016  # expanded by the render script
        printf '%s\n' 'bash -c '\''printf "%s\n" "${LLM_BACKEND:-<unset>}"'\'' > "$INSTALL_DIR/exported-llm-backend"'
    } > "$script"
    bash "$script" 2>"$install_dir/render.err"
}
