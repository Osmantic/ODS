#!/bin/bash
# ods-update.sh - ODS Update Manager
#
# Commands:
#   check      - Check for updates against GitHub releases
#   status     - Show current version, install path, last check
#   backup     - Backup compose files, .env, and version state
#   snapshot   - Create an integrity-checked pre-update rollback snapshot
#   update     - Pull new version, run migrations, restart services
#   rollback   - Restore from last backup
#   changelog  - Show version changelog
#   health     - Run health checks on all services

set -euo pipefail

# Prerequisites
command -v jq >/dev/null 2>&1 || { echo "Error: jq is required but not installed." >&2; echo "Install with: apt install jq (Debian/Ubuntu) or brew install jq (macOS)" >&2; exit 1; }

#==============================================================================
# CONFIGURATION
#==============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="${SCRIPT_DIR}"
VERSION_FILE="${INSTALL_DIR}/.version"
BACKUP_DIR="${HOME}/.ods/backups"
ROLLBACK_DIR="${INSTALL_DIR}/data/backups"   # pre-update rollback snapshots live here
MAX_BACKUPS="${MAX_BACKUPS:-10}"
UPDATE_CHANNEL="${UPDATE_CHANNEL:-stable}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-120}"
GITHUB_REPO="${GITHUB_REPO:-Osmantic/ODS}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Prerequisites check
command -v jq >/dev/null 2>&1 || { echo -e "${RED}Error: jq is required but not installed.${NC}" >&2; echo "Install with: apt install jq (Debian/Ubuntu) or brew install jq (macOS)" >&2; exit 1; }
command -v curl >/dev/null 2>&1 || { echo -e "${RED}Error: curl is required but not installed.${NC}" >&2; exit 1; }

#==============================================================================
# HELPER FUNCTIONS
#==============================================================================

log_info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
log_ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

get_current_version() {
    if [[ -f "$VERSION_FILE" ]]; then
        jq -r '.version // "0.0.0"' "$VERSION_FILE" 2>/dev/null || echo "0.0.0"
    else
        echo "0.0.0"
    fi
}

env_file_value() {
    local key="$1"
    [[ -f "${INSTALL_DIR}/.env" ]] || return 0
    awk -F= -v key="$key" '
        $1 == key {
            value = substr($0, index($0, "=") + 1)
            gsub(/\r$/, "", value)
            gsub(/^["'\'']|["'\'']$/, "", value)
            print value
            exit
        }
    ' "${INSTALL_DIR}/.env" 2>/dev/null || true
}

COMPOSE_PARSED_ARGS=()

compose_flags_parse() {
    local flags="$1" parsed="" token
    COMPOSE_PARSED_ARGS=()
    [[ -n "$flags" ]] || return 0
    # xargs tokenizes shell-style quotes without evaluating substitutions or
    # commands. One token per output line preserves whitespace inside a path;
    # compose filenames containing newlines are intentionally unsupported.
    parsed="$(printf '%s\n' "$flags" | xargs -n 1 printf '%s\n')" || return 1
    while IFS= read -r token; do
        [[ -n "$token" ]] && COMPOSE_PARSED_ARGS+=("$token")
    done <<< "$parsed"
}

compose_flags_files_exist() {
    local flags="$1" index path
    compose_flags_parse "$flags" || return 1
    for ((index = 0; index < ${#COMPOSE_PARSED_ARGS[@]}; index++)); do
        [[ "${COMPOSE_PARSED_ARGS[$index]}" == "-f" ]] || continue
        ((index + 1 < ${#COMPOSE_PARSED_ARGS[@]})) || return 1
        path="${COMPOSE_PARSED_ARGS[$((index + 1))]}"
        if [[ "$path" = /* ]]; then
            [[ -f "$path" ]] || return 1
        else
            [[ -f "${INSTALL_DIR}/${path}" ]] || return 1
        fi
    done
    return 0
}

resolve_compose_flags() {
    local cached=""
    if [[ -f "${INSTALL_DIR}/.compose-flags" ]]; then
        cached="$(< "${INSTALL_DIR}/.compose-flags")"
        if [[ -n "$cached" ]] && compose_flags_files_exist "$cached"; then
            printf '%s\n' "$cached"
            return 0
        fi
        log_warn "Cached compose flags are missing or stale; trying dynamic compose resolution." >&2
    fi

    local gpu_backend tier gpu_count ods_mode
    gpu_backend="$(env_file_value GPU_BACKEND)"
    tier="$(env_file_value TIER)"
    gpu_count="$(env_file_value GPU_COUNT)"
    ods_mode="$(env_file_value ODS_MODE)"

    local resolved=""
    if [[ -x "${INSTALL_DIR}/scripts/resolve-compose-stack.sh" ]]; then
        resolved=$(bash "${INSTALL_DIR}/scripts/resolve-compose-stack.sh" \
            --script-dir "$INSTALL_DIR" \
            --tier "${tier:-1}" \
            --gpu-backend "${gpu_backend:-nvidia}" \
            --gpu-count "${gpu_count:-1}" \
            --ods-mode "${ods_mode:-local}" | tail -1) || resolved=""
        if [[ -n "$resolved" ]] && compose_flags_files_exist "$resolved"; then
            echo "$resolved"
            return 0
        fi
    fi

    if [[ -f "${INSTALL_DIR}/docker-compose.yml" ]]; then
        echo "-f docker-compose.yml"
        return 0
    fi

    if [[ -f "${INSTALL_DIR}/docker-compose.base.yml" ]]; then
        local fallback="-f docker-compose.base.yml"
        case "${gpu_backend:-}" in
            nvidia|amd|cpu|apple|intel|sycl)
                if [[ -f "${INSTALL_DIR}/docker-compose.${gpu_backend}.yml" ]]; then
                    fallback="$fallback -f docker-compose.${gpu_backend}.yml"
                fi
                ;;
        esac
        echo "$fallback"
        return 0
    fi

    return 1
}

is_git_checkout() {
    command -v git >/dev/null 2>&1 || return 1
    git -C "${INSTALL_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1 || return 1

    local prefix top
    top=$(git -C "${INSTALL_DIR}" rev-parse --show-toplevel 2>/dev/null || return 1)
    prefix=$(git -C "${INSTALL_DIR}" rev-parse --show-prefix 2>/dev/null || return 1)
    git -C "${top}" ls-files --error-unmatch "${prefix}ods-update.sh" >/dev/null 2>&1
}

runtime_update_guidance() {
    log_info "For routine runtime/image updates, run: cd \"${INSTALL_DIR}\" && ./ods-cli update"
    log_info "For source-code updates, reinstall or run this command from a git-backed ODS source checkout."
}

ensure_source_checkout_for_update() {
    if ! command -v git >/dev/null 2>&1; then
        log_error "git is required for ods-update.sh source-code updates."
        runtime_update_guidance
        return 1
    fi

    if ! is_git_checkout; then
        log_error "ods-update.sh update only works from a git-backed ODS source checkout."
        log_info "Install path: ${INSTALL_DIR}"
        runtime_update_guidance
        log_info "No files, services, or rollback snapshots were changed."
        return 1
    fi
}

is_assistant_first_install() {
    [[ "$(env_file_value ODS_INSTALL_PROFILE)" == "assistant-first" ]]
}

_assistant_first_mutation_guard_held() {
    local descriptor="${ODS_MUTATION_GUARD_FD:-}" python_cmd=""
    [[ "$descriptor" =~ ^[0-9]+$ ]] || return 1
    [[ -f "${INSTALL_DIR}/scripts/run-with-extension-mutation-guard.py" \
        && ! -L "${INSTALL_DIR}/scripts/run-with-extension-mutation-guard.py" \
        && -f "${INSTALL_DIR}/lib/python-cmd.sh" \
        && ! -L "${INSTALL_DIR}/lib/python-cmd.sh" ]] || return 1
    # The marker is not trusted by itself. Bind the inherited descriptor to the
    # canonical guard inode and confirm (or safely acquire) its live flock.
    # shellcheck source=lib/python-cmd.sh
    . "${INSTALL_DIR}/lib/python-cmd.sh" >/dev/null 2>&1 || return 1
    python_cmd=$(ods_detect_python_cmd 2>/dev/null || true)
    [[ -n "$python_cmd" ]] || return 1
    "$python_cmd" "${INSTALL_DIR}/scripts/run-with-extension-mutation-guard.py" \
        --lock-parent "${INSTALL_DIR}/data" \
        --verify-held-fd "$descriptor" >/dev/null 2>&1
}

_run_with_assistant_first_mutation_guard() {
    local command="$1" python_cmd="" guard_status=0
    shift || true

    [[ -f "${INSTALL_DIR}/scripts/run-with-extension-mutation-guard.py" \
        && ! -L "${INSTALL_DIR}/scripts/run-with-extension-mutation-guard.py" ]] || {
        log_error "Assistant First update coordination support is unavailable."
        return 74
    }
    [[ -f "${INSTALL_DIR}/lib/python-cmd.sh" \
        && ! -L "${INSTALL_DIR}/lib/python-cmd.sh" ]] || {
        log_error "Python command resolution is unavailable for update coordination."
        return 74
    }

    # shellcheck source=lib/python-cmd.sh
    if ! . "${INSTALL_DIR}/lib/python-cmd.sh"; then
        log_error "Python command resolution could not be loaded."
        return 74
    fi
    python_cmd=$(ods_detect_python_cmd 2>/dev/null || true)
    [[ -n "$python_cmd" ]] || {
        log_error "A runnable Python interpreter is required for update coordination."
        return 74
    }

    # Replace this top-level updater process so terminal signals retain the
    # same PID after the Python lock holder replaces itself with guarded Bash.
    # Spawning a child here would strand the recovery traps in the child while
    # HUP/INT/TERM terminates the waiting parent.
    exec "$python_cmd" "${INSTALL_DIR}/scripts/run-with-extension-mutation-guard.py" \
        --lock-parent "${INSTALL_DIR}/data" \
        --timeout "${ODS_MUTATION_GUARD_TIMEOUT:-5}" \
        -- bash "${SCRIPT_DIR}/ods-update.sh" "$command" "$@" \
        || guard_status=$?
    return "$guard_status"
}

# Assistant First source updates are resolved in a disposable repository so
# fetching and inspecting a candidate cannot mutate the installed checkout.
# The exact object is imported into the installed repository only after the
# compatibility gate succeeds and the rollback snapshot is complete.
UPDATE_CANDIDATE_ROOT=""
UPDATE_CANDIDATE_TEMP_BASE=""
UPDATE_CANDIDATE_REPOSITORY=""
UPDATE_CANDIDATE_TREE=""
UPDATE_CANDIDATE_REVISION=""
UPDATE_CANDIDATE_BRANCH=""
UPDATE_CANDIDATE_PREFLIGHT=""
UPDATE_CANDIDATE_PREFLIGHT_HASH=""

# Assistant First source-state bindings survive disposable candidate cleanup so
# a later migration or health failure can restore the exact pre-update source.
UPDATE_SOURCE_ORIGINAL_HEAD=""
UPDATE_SOURCE_ORIGINAL_BRANCH=""
UPDATE_SOURCE_ORIGINAL_UPSTREAM=""
UPDATE_SOURCE_LOCKFILE_HASH=""
UPDATE_SOURCE_APPLIED_HEAD=""
UPDATE_SIGNAL_SNAPSHOT=""
UPDATE_SIGNAL_COMPOSE_FLAGS=""
UPDATE_SIGNAL_MUTATION_STARTED=false

_cleanup_update_candidate() {
    local root="${UPDATE_CANDIDATE_ROOT:-}"
    local temp_base="${UPDATE_CANDIDATE_TEMP_BASE:-}"

    UPDATE_CANDIDATE_ROOT=""
    UPDATE_CANDIDATE_TEMP_BASE=""
    UPDATE_CANDIDATE_REPOSITORY=""
    UPDATE_CANDIDATE_TREE=""
    UPDATE_CANDIDATE_REVISION=""
    UPDATE_CANDIDATE_BRANCH=""
    UPDATE_CANDIDATE_PREFLIGHT=""
    UPDATE_CANDIDATE_PREFLIGHT_HASH=""

    [[ -n "$root" && -n "$temp_base" ]] || return 0
    case "$root" in
        "$temp_base"/ods-update-candidate.*)
            rm -rf -- "$root"
            ;;
        *)
            log_warn "Refusing to remove an unexpected candidate workspace."
            ;;
    esac
}

_update_file_sha256() {
    local path="$1" digest=""
    [[ -f "$path" ]] || return 1
    if command -v sha256sum >/dev/null 2>&1; then
        digest=$(sha256sum "$path" 2>/dev/null | awk '{print $1}')
    elif command -v shasum >/dev/null 2>&1; then
        digest=$(shasum -a 256 "$path" 2>/dev/null | awk '{print $1}')
    else
        return 1
    fi
    [[ "$digest" =~ ^[0-9a-f]{64}$ ]] || return 1
    printf '%s\n' "$digest"
}

_update_source_branch() {
    local branch=""
    branch=$(git -C "$INSTALL_DIR" symbolic-ref --quiet HEAD \
        2>/dev/null || true)
    if [[ -n "$branch" ]]; then
        printf 'branch:%s\n' "$branch"
    else
        printf '%s\n' detached
    fi
}

_update_source_upstream() {
    local upstream=""
    upstream=$(git -C "$INSTALL_DIR" rev-parse --abbrev-ref \
        --symbolic-full-name '@{upstream}' 2>/dev/null || true)
    if [[ -n "$upstream" ]]; then
        printf 'upstream:%s\n' "$upstream"
    else
        printf '%s\n' none
    fi
}

_update_tracked_tree_clean() {
    git -C "$INSTALL_DIR" diff --quiet --ignore-submodules -- \
        && git -C "$INSTALL_DIR" diff --cached --quiet --ignore-submodules --
}

_capture_assistant_first_source_state() {
    local head_revision lockfile_path

    if ! _update_tracked_tree_clean; then
        log_error "Assistant First source updates require a clean tracked checkout."
        log_info "Commit or restore staged and unstaged tracked changes before retrying."
        return 1
    fi

    head_revision=$(git -C "$INSTALL_DIR" rev-parse --verify 'HEAD^{commit}' \
        2>/dev/null || true)
    if [[ ! "$head_revision" =~ ^[0-9a-f]{40}([0-9a-f]{24})?$ ]]; then
        log_error "Could not bind the installed source revision."
        return 1
    fi

    lockfile_path="${INSTALL_DIR}/data/assistant-first/desired-state/extensions.lock.json"
    UPDATE_SOURCE_LOCKFILE_HASH=$(_update_file_sha256 "$lockfile_path") || {
        log_error "Could not bind the canonical Assistant First lockfile."
        return 1
    }
    UPDATE_SOURCE_ORIGINAL_HEAD="$head_revision"
    UPDATE_SOURCE_ORIGINAL_BRANCH=$(_update_source_branch)
    UPDATE_SOURCE_ORIGINAL_UPSTREAM=$(_update_source_upstream)
    UPDATE_SOURCE_APPLIED_HEAD=""
}

_verify_assistant_first_source_state() {
    local current_head current_lockfile_hash lockfile_path

    current_head=$(git -C "$INSTALL_DIR" rev-parse --verify 'HEAD^{commit}' \
        2>/dev/null || true)
    if [[ "$current_head" != "$UPDATE_SOURCE_ORIGINAL_HEAD" ]] \
       || [[ "$(_update_source_branch)" != "$UPDATE_SOURCE_ORIGINAL_BRANCH" ]] \
       || [[ "$(_update_source_upstream)" != "$UPDATE_SOURCE_ORIGINAL_UPSTREAM" ]]; then
        log_error "The installed source checkout changed after update preflight."
        log_info "No candidate object or source change was applied. Retry from a stable checkout."
        return 1
    fi
    if ! _update_tracked_tree_clean; then
        log_error "Tracked source files changed after update preflight."
        log_info "No candidate object or source change was applied. Retry from a clean checkout."
        return 1
    fi

    lockfile_path="${INSTALL_DIR}/data/assistant-first/desired-state/extensions.lock.json"
    current_lockfile_hash=$(_update_file_sha256 "$lockfile_path" 2>/dev/null || true)
    if [[ "$current_lockfile_hash" != "$UPDATE_SOURCE_LOCKFILE_HASH" ]]; then
        log_error "Assistant First desired state changed after update preflight."
        log_info "No candidate object or source change was applied. Retry after the extension operation finishes."
        return 1
    fi
}

_restore_assistant_first_source() {
    local revision="$1" current_head current_branch current_upstream restored_head
    if [[ ! "$revision" =~ ^[0-9a-f]{40}([0-9a-f]{24})?$ ]] \
       || ! git -C "$INSTALL_DIR" cat-file -e "${revision}^{commit}" 2>/dev/null; then
        log_error "CRITICAL: The recorded source rollback revision is invalid."
        return 1
    fi
    current_head=$(git -C "$INSTALL_DIR" rev-parse --verify 'HEAD^{commit}' \
        2>/dev/null || true)
    if [[ "$current_head" != "$revision" ]] \
       && { [[ -z "$UPDATE_SOURCE_APPLIED_HEAD" ]] \
            || [[ "$current_head" != "$UPDATE_SOURCE_APPLIED_HEAD" ]]; }; then
        log_error "CRITICAL: Installed HEAD no longer matches the applied update candidate."
        log_info "Refusing to erase an unexpected source revision; manual recovery is required."
        return 1
    fi
    current_branch=$(_update_source_branch)
    current_upstream=$(_update_source_upstream)
    if [[ "$current_branch" != "$UPDATE_SOURCE_ORIGINAL_BRANCH" ]] \
       || [[ "$current_upstream" != "$UPDATE_SOURCE_ORIGINAL_UPSTREAM" ]]; then
        log_error "CRITICAL: Installed branch or upstream changed after the update candidate was applied."
        log_info "Refusing to reset an unexpected source checkout; manual recovery is required."
        return 1
    fi
    if ! _update_tracked_tree_clean; then
        log_error "CRITICAL: Tracked source files changed after the update candidate was applied."
        log_info "Refusing to erase concurrent tracked changes; manual recovery is required."
        return 1
    fi
    if ! git -C "$INSTALL_DIR" reset --hard "$revision" >/dev/null 2>&1; then
        log_error "CRITICAL: Could not restore source revision ${revision}."
        return 1
    fi
    restored_head=$(git -C "$INSTALL_DIR" rev-parse --verify 'HEAD^{commit}' \
        2>/dev/null || true)
    if [[ "$restored_head" != "$revision" ]] || ! _update_tracked_tree_clean; then
        log_error "CRITICAL: Source rollback verification failed."
        return 1
    fi
    log_ok "Restored source revision ${revision}."
}

_clear_assistant_first_source_state() {
    UPDATE_SOURCE_ORIGINAL_HEAD=""
    UPDATE_SOURCE_ORIGINAL_BRANCH=""
    UPDATE_SOURCE_ORIGINAL_UPSTREAM=""
    UPDATE_SOURCE_LOCKFILE_HASH=""
    UPDATE_SOURCE_APPLIED_HEAD=""
}

_arm_assistant_first_update_signal_rollback() {
    UPDATE_SIGNAL_SNAPSHOT="$1"
    UPDATE_SIGNAL_COMPOSE_FLAGS="$2"
    UPDATE_SIGNAL_MUTATION_STARTED=false
    trap '_assistant_first_update_interrupted HUP' HUP
    trap '_assistant_first_update_interrupted INT' INT
    trap '_assistant_first_update_interrupted TERM' TERM
}

_disarm_assistant_first_update_signal_rollback() {
    trap '_cleanup_update_candidate; exit 130' HUP INT TERM
    UPDATE_SIGNAL_SNAPSHOT=""
    UPDATE_SIGNAL_COMPOSE_FLAGS=""
    UPDATE_SIGNAL_MUTATION_STARTED=false
}

_assistant_first_update_interrupted() {
    local signal="${1:-INT}" current_head="" rollback_source="" status=130

    # Bash runs traps between commands. Disable re-entry first, then infer a
    # completed fast-forward even if the signal landed before cmd_update could
    # record UPDATE_SOURCE_APPLIED_HEAD.
    trap '' HUP INT TERM
    log_error "Assistant First update interrupted by ${signal}."
    current_head=$(git -C "$INSTALL_DIR" rev-parse --verify 'HEAD^{commit}' \
        2>/dev/null || true)
    if [[ -n "$UPDATE_CANDIDATE_REVISION" ]] \
       && [[ "$current_head" == "$UPDATE_CANDIDATE_REVISION" ]] \
       && [[ "$current_head" != "$UPDATE_SOURCE_ORIGINAL_HEAD" ]]; then
        UPDATE_SOURCE_APPLIED_HEAD="$current_head"
        UPDATE_SIGNAL_MUTATION_STARTED=true
    fi
    _cleanup_update_candidate

    if $UPDATE_SIGNAL_MUTATION_STARTED; then
        [[ -n "$UPDATE_SOURCE_APPLIED_HEAD" ]] \
            && rollback_source="$UPDATE_SOURCE_ORIGINAL_HEAD"
        if ! _update_rollback "Assistant First update interrupted by ${signal}." \
            "$UPDATE_SIGNAL_SNAPSHOT" "$UPDATE_SIGNAL_COMPOSE_FLAGS" \
            "$rollback_source"; then
            log_error "Interrupted update recovery did not complete; manual recovery is required."
        fi
    else
        log_info "The interrupt arrived before installed source or runtime mutation."
    fi

    case "$signal" in
        HUP) status=129 ;;
        TERM) status=143 ;;
    esac
    exit "$status"
}

_assistant_first_source_branch() {
    local upstream="" branch=""
    upstream=$(git -C "$INSTALL_DIR" rev-parse \
        --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true)
    case "$upstream" in
        origin/*)
            branch="${upstream#origin/}"
            if git -C "$INSTALL_DIR" check-ref-format --branch "$branch" \
                >/dev/null 2>&1; then
                printf '%s\n' "$branch"
                return 0
            fi
            return 2
            ;;
        "") return 1 ;;
        *) return 2 ;;
    esac
}

_prepare_assistant_first_update_candidate() {
    local temp_base origin_url object_format python_cmd source_prefix source_root
    local manifest_object catalog_object current_revision branch status error_code
    local preflight_hash
    local -a branch_candidates=()

    [[ -f "${INSTALL_DIR}/scripts/assess-extension-update.py" \
        && ! -L "${INSTALL_DIR}/scripts/assess-extension-update.py" ]] || {
        log_error "Assistant First update compatibility support is unavailable."
        return 1
    }
    [[ -f "${INSTALL_DIR}/lib/python-cmd.sh" \
        && ! -L "${INSTALL_DIR}/lib/python-cmd.sh" ]] || {
        log_error "Python command resolution is unavailable for update compatibility."
        return 1
    }

    # shellcheck source=lib/python-cmd.sh
    if ! . "${INSTALL_DIR}/lib/python-cmd.sh"; then
        log_error "Python command resolution could not be loaded."
        return 1
    fi
    python_cmd=$(ods_detect_python_cmd 2>/dev/null || true)
    [[ -n "$python_cmd" ]] || {
        log_error "A runnable Python interpreter is required for update compatibility."
        return 1
    }

    origin_url=$(git -C "$INSTALL_DIR" remote get-url origin 2>/dev/null || true)
    [[ -n "$origin_url" ]] || {
        log_error "The installed checkout has no usable origin remote."
        return 1
    }

    temp_base=$(cd "${TMPDIR:-/tmp}" 2>/dev/null && pwd -P) || {
        log_error "The temporary directory is unavailable for candidate inspection."
        return 1
    }
    UPDATE_CANDIDATE_TEMP_BASE="$temp_base"
    UPDATE_CANDIDATE_ROOT=$(mktemp -d \
        "${temp_base}/ods-update-candidate.XXXXXXXX") || {
        log_error "Could not create a private candidate workspace."
        return 1
    }
    chmod 700 "$UPDATE_CANDIDATE_ROOT" || {
        _cleanup_update_candidate
        log_error "Could not secure the candidate workspace."
        return 1
    }
    trap '_cleanup_update_candidate' EXIT
    trap '_cleanup_update_candidate; exit 130' HUP INT TERM

    UPDATE_CANDIDATE_REPOSITORY="${UPDATE_CANDIDATE_ROOT}/repository.git"
    UPDATE_CANDIDATE_TREE="${UPDATE_CANDIDATE_ROOT}/tree"
    UPDATE_CANDIDATE_PREFLIGHT="${UPDATE_CANDIDATE_ROOT}/preflight.json"
    if ! mkdir "$UPDATE_CANDIDATE_TREE" || ! chmod 700 "$UPDATE_CANDIDATE_TREE"; then
        _cleanup_update_candidate
        log_error "Could not secure the materialized candidate tree."
        return 1
    fi

    object_format=$(git -C "$INSTALL_DIR" rev-parse --show-object-format \
        2>/dev/null || printf '%s' sha1)
    case "$object_format" in
        sha1)
            if ! git init -q --bare "$UPDATE_CANDIDATE_REPOSITORY"; then
                _cleanup_update_candidate
                log_error "Could not initialize the candidate repository."
                return 1
            fi
            ;;
        sha256)
            if ! git init -q --bare --object-format=sha256 \
                "$UPDATE_CANDIDATE_REPOSITORY"; then
                _cleanup_update_candidate
                log_error "Could not initialize the candidate repository."
                return 1
            fi
            ;;
        *)
            _cleanup_update_candidate
            log_error "The checkout uses an unsupported Git object format."
            return 1
            ;;
    esac

    if branch=$(_assistant_first_source_branch); then
        branch_candidates+=("$branch")
    else
        status=$?
        if [[ "$status" -eq 2 ]]; then
            _cleanup_update_candidate
            log_error "The configured source upstream must use the origin remote."
            log_info "Refusing to substitute origin/main or origin/master for another remote."
            return 1
        fi
        branch_candidates+=(main master)
    fi

    for branch in "${branch_candidates[@]}"; do
        if git -C "$UPDATE_CANDIDATE_REPOSITORY" fetch -q --no-tags \
            -- "$origin_url" \
            "+refs/heads/${branch}:refs/heads/candidate" \
            2>"${UPDATE_CANDIDATE_ROOT}/fetch-error.log"; then
            UPDATE_CANDIDATE_BRANCH="$branch"
            break
        fi
    done
    if [[ -z "$UPDATE_CANDIDATE_BRANCH" ]]; then
        _cleanup_update_candidate
        log_error "Could not fetch the configured source branch, main, or master."
        return 1
    fi

    UPDATE_CANDIDATE_REVISION=$(git -C "$UPDATE_CANDIDATE_REPOSITORY" \
        rev-parse --verify 'refs/heads/candidate^{commit}' 2>/dev/null || true)
    if [[ ! "$UPDATE_CANDIDATE_REVISION" =~ ^[0-9a-f]{40}([0-9a-f]{24})?$ ]]; then
        _cleanup_update_candidate
        log_error "The fetched update candidate has no exact Git object ID."
        return 1
    fi

    current_revision=$(git -C "$INSTALL_DIR" rev-parse --verify 'HEAD^{commit}' \
        2>/dev/null || true)
    if ! git -C "$UPDATE_CANDIDATE_REPOSITORY" merge-base --is-ancestor \
        "$current_revision" "$UPDATE_CANDIDATE_REVISION" 2>/dev/null; then
        _cleanup_update_candidate
        log_error "The candidate is not a fast-forward of the installed checkout."
        log_info "Resolve local commits or branch divergence before retrying."
        return 1
    fi

    source_prefix=$(git -C "$INSTALL_DIR" rev-parse --show-prefix 2>/dev/null || true)
    source_prefix="${source_prefix%/}"
    source_root=""
    if [[ -n "$source_prefix" ]]; then
        source_root="${source_prefix}/"
    fi
    manifest_object="${UPDATE_CANDIDATE_REVISION}:${source_root}manifest.json"
    catalog_object="${UPDATE_CANDIDATE_REVISION}:${source_root}config/extensions-catalog.json"

    # Read the exact committed blob bytes. `git archive` is intentionally not
    # used here because a candidate-controlled `export-subst` attribute can
    # transform archive output so it differs from the later checked-out commit.
    if ! mkdir "${UPDATE_CANDIDATE_TREE}/config" || \
       ! chmod 700 "${UPDATE_CANDIDATE_TREE}/config" || \
       [[ "$(git -C "$UPDATE_CANDIDATE_REPOSITORY" cat-file -t \
            "$manifest_object" 2>/dev/null || true)" != blob ]] || \
       [[ "$(git -C "$UPDATE_CANDIDATE_REPOSITORY" cat-file -t \
            "$catalog_object" 2>/dev/null || true)" != blob ]] || \
       ! git -C "$UPDATE_CANDIDATE_REPOSITORY" cat-file blob \
            "$manifest_object" > "${UPDATE_CANDIDATE_TREE}/manifest.json" || \
       ! git -C "$UPDATE_CANDIDATE_REPOSITORY" cat-file blob \
            "$catalog_object" > \
            "${UPDATE_CANDIDATE_TREE}/config/extensions-catalog.json" || \
       ! chmod 600 "${UPDATE_CANDIDATE_TREE}/manifest.json" \
            "${UPDATE_CANDIDATE_TREE}/config/extensions-catalog.json"; then
        _cleanup_update_candidate
        log_error "Could not materialize the exact candidate metadata."
        return 1
    fi

    log_info "Assessing Assistant First update candidate ${UPDATE_CANDIDATE_REVISION}..."
    set +e
    "$python_cmd" "${INSTALL_DIR}/scripts/assess-extension-update.py" \
        --install-dir "$INSTALL_DIR" \
        --candidate-dir "$UPDATE_CANDIDATE_TREE" \
        --candidate-revision "$UPDATE_CANDIDATE_REVISION" \
        > "$UPDATE_CANDIDATE_PREFLIGHT"
    status=$?
    set -e

    case "$status" in
        0)
            preflight_hash=$(jq -r '.preflightHash // empty' \
                "$UPDATE_CANDIDATE_PREFLIGHT" 2>/dev/null || true)
            if [[ "$(jq -r '.schema // empty' "$UPDATE_CANDIDATE_PREFLIGHT" \
                    2>/dev/null || true)" != \
                    "ods.extensions.update-preflight.v1" ]] || \
               [[ "$(jq -r '.candidateSourceRevision // empty' \
                    "$UPDATE_CANDIDATE_PREFLIGHT" 2>/dev/null || true)" != \
                    "$UPDATE_CANDIDATE_REVISION" ]] || \
               [[ ! "$preflight_hash" =~ ^[0-9a-f]{64}$ ]]; then
                _cleanup_update_candidate
                log_error "The compatibility gate returned an invalid success result."
                return 1
            fi
            UPDATE_CANDIDATE_PREFLIGHT_HASH="$preflight_hash"
            log_ok "Compatibility preflight accepted the exact candidate."
            log_info "Preflight hash: ${UPDATE_CANDIDATE_PREFLIGHT_HASH}"
            ;;
        10)
            _cleanup_update_candidate
            log_error "The core update requires a separately approved extension plan."
            log_info "Review and approve the exact extension upgrade plan, then retry."
            return 1
            ;;
        11)
            _cleanup_update_candidate
            log_error "Installed extension compatibility blocks this core update."
            log_info "Resolve the reported extension compatibility blockers before retrying."
            return 1
            ;;
        12)
            error_code=$(jq -r '.error.code // "invalid-input"' \
                "$UPDATE_CANDIDATE_PREFLIGHT" 2>/dev/null || printf '%s' invalid-input)
            _cleanup_update_candidate
            log_error "Assistant First update preflight failed closed (${error_code})."
            log_info "Verify the owner-private desired-state lockfile and candidate metadata."
            return 1
            ;;
        *)
            _cleanup_update_candidate
            log_error "Assistant First update preflight returned an unsupported status."
            return 1
            ;;
    esac
}

_apply_assistant_first_update_candidate() {
    local fetched_revision head_revision

    if ! git -C "$INSTALL_DIR" fetch -q --no-tags \
        -- "$UPDATE_CANDIDATE_REPOSITORY" refs/heads/candidate; then
        return 1
    fi
    fetched_revision=$(git -C "$INSTALL_DIR" rev-parse --verify \
        'FETCH_HEAD^{commit}' 2>/dev/null || true)
    [[ "$fetched_revision" == "$UPDATE_CANDIDATE_REVISION" ]] || return 1

    git -C "$INSTALL_DIR" merge --ff-only "$UPDATE_CANDIDATE_REVISION" \
        >/dev/null 2>&1 || return 1
    head_revision=$(git -C "$INSTALL_DIR" rev-parse --verify 'HEAD^{commit}' \
        2>/dev/null || true)
    [[ "$head_revision" == "$UPDATE_CANDIDATE_REVISION" ]] \
        && _update_tracked_tree_clean
}

# Semver compare: returns 0 if equal, 1 if v1 > v2, 2 if v1 < v2
semver_compare() {
    local v1="${1#v}"
    local v2="${2#v}"
    
    if [[ "$v1" == "$v2" ]]; then
        return 0
    fi
    
    local IFS='.'
    local i v1_parts=($v1) v2_parts=($v2)
    
    for ((i=0; i<3; i++)); do
        local n1="${v1_parts[$i]:-0}"
        local n2="${v2_parts[$i]:-0}"
        # Strip any non-numeric suffix
        n1="${n1%%[!0-9]*}"
        n2="${n2%%[!0-9]*}"
        
        if ((n1 > n2)); then
            return 1
        elif ((n1 < n2)); then
            return 2
        fi
    done
    return 0
}

# _prune_rollback_snapshots
#   Removes oldest pre-update snapshots beyond MAX_BACKUPS.
#   Guards against misconfigured ROLLBACK_DIR before any rm -rf.
_prune_rollback_snapshots() {
    if [[ -z "$ROLLBACK_DIR" || "$ROLLBACK_DIR" != */data/backups ]]; then
        log_warn "ROLLBACK_DIR '${ROLLBACK_DIR}' does not end in /data/backups; skipping prune." >&2
        return 0
    fi
    [[ -d "$ROLLBACK_DIR" ]] || return 0
    local count=0
    while IFS= read -r old_snap; do
        count=$(( count + 1 ))
        if (( count > MAX_BACKUPS )); then
            log_info "Pruning old rollback snapshot: $(basename "$old_snap")" >&2
            rm -rf "$old_snap"
        fi
    done < <(find "${ROLLBACK_DIR}" -maxdepth 1 -type d -name "pre-update-*" | sort -r)
}

_load_update_snapshot_contract() {
    if declare -F snapshot_pre_update >/dev/null 2>&1; then
        return 0
    fi
    local library="${SCRIPT_DIR}/lib/update-snapshots.sh"
    if [[ ! -r "$library" || -L "$library" ]]; then
        log_error "Secure update snapshot support is unavailable: ${library}"
        return 1
    fi
    # shellcheck source=lib/update-snapshots.sh
    . "$library"
}

# wait_for_healthy
#   Polls cmd_health every 10 s until it passes or HEALTH_TIMEOUT expires.
#   Health output is captured to a temp log; shown in full only on timeout.
#   Returns 0 on success, 1 on timeout.
wait_for_healthy() {
    local deadline=$(( SECONDS + HEALTH_TIMEOUT ))
    local attempt=0
    local delay=10
    local health_log
    health_log=$(mktemp "${TMPDIR:-/tmp}/ods-health-XXXXXX.log")

    log_info "Waiting for services (timeout: ${HEALTH_TIMEOUT}s)..."

    while (( SECONDS < deadline )); do
        attempt=$(( attempt + 1 ))
        if cmd_health > "$health_log" 2>&1; then
            log_ok "Services healthy after ${attempt} attempt(s)."
            rm -f "$health_log"
            return 0
        fi
        local remaining=$(( deadline - SECONDS ))
        if (( remaining > delay )); then
            log_info "  Not yet healthy — retrying in ${delay}s (${remaining}s remaining)..."
            sleep "$delay"
        elif (( remaining > 0 )); then
            sleep "$remaining"
        fi
    done

    log_error "Health-check timeout after ${HEALTH_TIMEOUT}s. Final status:"
    cat "$health_log"
    rm -f "$health_log"
    return 1
}

# _update_rollback <reason> <snap_dir> [compose_flags] [source_revision]
#   Restores an optional validated source revision, then the snapshot, and
#   restarts services. Legacy callers omit source_revision and are unchanged.
#   Called when cmd_update encounters a non-zero exit at any step.
_update_rollback() {
    local reason="$1"
    local snap_dir_arg="$2"
    local compose_flags_arg="${3:-}"
    local source_revision="${4:-}"

    log_error "${reason}"
    if [[ -n "$source_revision" ]] \
       && ! _restore_assistant_first_source "$source_revision"; then
        log_error "CRITICAL: Source restore failed. Manual recovery required."
        log_error "  Source   : ${source_revision}"
        log_error "  Snapshot : ${snap_dir_arg}"
        return 1
    fi
    log_warn "Auto-restoring rollback snapshot and restarting services..."

    if ! _restore_snapshot "$snap_dir_arg"; then
        log_error "CRITICAL: Snapshot restore failed. Manual recovery required."
        log_error "  Snapshot : ${snap_dir_arg}"
        log_error "  Steps    :"
        log_error "    1. cp \"${snap_dir_arg}/.env\" \"${INSTALL_DIR}/.env\""
        log_error "    2. cd \"${INSTALL_DIR}\" && docker compose up -d"
        return 1
    fi

    cd "$INSTALL_DIR"
    if [[ -n "${compose_flags_arg}" ]]; then
        if ! docker compose ${compose_flags_arg} down --remove-orphans; then
            log_warn "docker compose v2 down failed, trying v1..."
            docker-compose ${compose_flags_arg} down --remove-orphans
        fi
        if ! docker compose ${compose_flags_arg} up -d; then
            log_warn "docker compose v2 up failed, trying v1..."
            docker-compose ${compose_flags_arg} up -d
        fi
    else
        if ! docker compose down --remove-orphans; then
            log_warn "docker compose v2 down failed, trying v1..."
            docker-compose down --remove-orphans
        fi
        if ! docker compose up -d; then
            log_warn "docker compose v2 up failed, trying v1..."
            docker-compose up -d
        fi
    fi
    log_warn "Rollback complete. Run 'ods-update.sh health' to verify."
}

#==============================================================================
# COMMAND: CHECK
#==============================================================================

cmd_check() {
    log_info "Checking for updates..."
    
    local current_version
    current_version=$(get_current_version)
    log_info "Current version: ${current_version}"
    
    # Fetch latest release from GitHub
    local api_url="https://api.github.com/repos/${GITHUB_REPO}/releases/latest"
    local response
    local curl_args=(-sf --max-time 15)
    if [[ -n "${GITHUB_TOKEN:-}" ]]; then
        curl_args+=(-H "Authorization: Bearer ${GITHUB_TOKEN}")
    fi

    if ! response=$(curl "${curl_args[@]}" "${api_url}" 2>/dev/null); then
        log_error "Failed to check for updates. Check network or GITHUB_TOKEN."
        return 1
    fi
    
    local latest_version
    latest_version=$(echo "$response" | jq -r '.tag_name // empty')
    
    if [[ -z "$latest_version" ]]; then
        log_warn "No releases found on GitHub. You may be on a development version."
        return 0
    fi
    
    log_info "Latest version: ${latest_version}"
    
    # Compare versions
    set +e
    semver_compare "$current_version" "$latest_version"
    local cmp_result=$?
    set -e
    
    case $cmp_result in
        0)
            log_ok "You are on the latest version."
            ;;
        1)
            log_warn "You are ahead of the latest release (development version)."
            ;;
        2)
            log_info "Update available: ${current_version} → ${latest_version}"
            echo ""
            echo "Run 'ods update' or './ods-cli update' for normal runtime updates."
            if is_git_checkout; then
                echo "Source checkout detected: run 'ods-update.sh update' only when you intend to pull source code."
            fi
            ;;
    esac
    
    # Update last check timestamp
    mkdir -p "$(dirname "$VERSION_FILE")"
    local version_data
    if [[ -f "$VERSION_FILE" ]]; then
        version_data=$(cat "$VERSION_FILE")
    else
        version_data='{}'
    fi
    local tmp_version_file
    tmp_version_file=$(mktemp "${VERSION_FILE}.tmp.XXXXXX")
    echo "$version_data" | jq --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '.last_check = $ts' > "$tmp_version_file"
    mv -f "$tmp_version_file" "$VERSION_FILE"
}

#==============================================================================
# COMMAND: STATUS
#==============================================================================

cmd_status() {
    echo "ODS Status"
    echo "==================="
    echo ""
    echo "Version:        $(get_current_version)"
    echo "Install path:   ${INSTALL_DIR}"
    echo "Backup path:    ${BACKUP_DIR}"
    echo "Update channel: ${UPDATE_CHANNEL}"
    echo ""
    
    if [[ -f "$VERSION_FILE" ]]; then
        local last_check
        last_check=$(jq -r '.last_check // "never"' "$VERSION_FILE" 2>/dev/null || echo "never")
        local last_update
        last_update=$(jq -r '.last_update // "never"' "$VERSION_FILE" 2>/dev/null || echo "never")
        echo "Last check:     ${last_check}"
        echo "Last update:    ${last_update}"
    else
        echo "Last check:     never"
        echo "Last update:    never"
    fi
    
    echo ""
    
    # Count rollback snapshots
    local snap_count=0
    if [[ -d "$ROLLBACK_DIR" ]]; then
        snap_count=$(find "$ROLLBACK_DIR" -maxdepth 1 -type d -name "pre-update-*" 2>/dev/null | wc -l)
    fi
    echo "Rollback snaps: ${snap_count} (max: ${MAX_BACKUPS}, path: ${ROLLBACK_DIR})"

    # Show last rollback point recorded in version file
    if [[ -f "$VERSION_FILE" ]]; then
        local last_snap
        last_snap=$(jq -r '.last_rollback_point // "none"' "$VERSION_FILE" 2>/dev/null || echo "none")
        echo "Last snap path: ${last_snap}"
    fi

    echo ""

    # Count general backups
    if [[ -d "$BACKUP_DIR" ]]; then
        local backup_count
        backup_count=$(find "$BACKUP_DIR" -maxdepth 1 -type d -name "backup-*" 2>/dev/null | wc -l)
        echo "General backups: ${backup_count} (max: ${MAX_BACKUPS}, path: ${BACKUP_DIR})"
    else
        echo "General backups: 0 (max: ${MAX_BACKUPS})"
    fi
}

#==============================================================================
# COMMAND: BACKUP
#==============================================================================

cmd_backup() {
    local backup_name="${1:-}"
    local timestamp
    timestamp=$(date +%Y%m%d-%H%M%S)
    local backup_id="backup-${timestamp}"
    
    if [[ -n "$backup_name" ]]; then
        backup_id="backup-${backup_name}-${timestamp}"
    fi
    
    local backup_path="${BACKUP_DIR}/${backup_id}"
    
    log_info "Creating backup: ${backup_id}"
    
    mkdir -p "$backup_path"
    
    # Backup compose files
    # NB: x=$((x + 1)) not ((x++)) — the post-increment form evaluates to 0
    # on the first increment, which set -e treats as failure and aborts the
    # backup after copying a single file.
    local files_backed_up=0
    for pattern in "docker-compose*.yml" "docker-compose*.yaml" ".env" ".env.*"; do
        for file in "${INSTALL_DIR}"/${pattern}; do
            if [[ -f "$file" ]]; then
                cp "$file" "$backup_path/"
                files_backed_up=$((files_backed_up + 1))
            fi
        done
    done

    # Backup version file
    if [[ -f "$VERSION_FILE" ]]; then
        cp "$VERSION_FILE" "$backup_path/.version"
        files_backed_up=$((files_backed_up + 1))
    fi
    
    # Generate metadata (use jq for safe JSON construction)
    jq -n \
        --arg bid "$backup_id" \
        --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --arg ver "$(get_current_version)" \
        --argjson fc "$files_backed_up" \
        --arg dir "$INSTALL_DIR" \
        '{backup_id: $bid, timestamp: $ts, version: $ver, files_count: $fc, install_dir: $dir}' \
        > "$backup_path/metadata.json"
    
    log_ok "Backup created: ${backup_path}"
    log_info "Files backed up: ${files_backed_up}"
    
    # Bash's sorted glob preserves whole paths, including spaces/newlines,
    # without requiring GNU sort -z on macOS. Never follow backup symlinks.
    local backup_dirs=() dir index count=0
    for dir in "$BACKUP_DIR"/backup-*; do
        [[ -d "$dir" && ! -L "$dir" ]] || continue
        backup_dirs+=("$dir")
    done
    for ((index=${#backup_dirs[@]}-1; index>=0; index--)); do
        dir="${backup_dirs[$index]}"
        count=$((count + 1))
        if ((count > MAX_BACKUPS)); then
            log_info "Removing old backup: $(basename "$dir")"
            rm -rf "$dir"
        fi
    done
}

#==============================================================================
# COMMAND: UPDATE
#==============================================================================

cmd_snapshot() {
    local timestamp="${1:-$(date +%Y%m%d-%H%M%S)}"
    _load_update_snapshot_contract || return 1
    snapshot_pre_update "$timestamp"
}

cmd_update() {
    local mutation_guard_status=0
    if is_assistant_first_install && ! _assistant_first_mutation_guard_held; then
        _run_with_assistant_first_mutation_guard update "$@" \
            || mutation_guard_status=$?
        return "$mutation_guard_status"
    fi

    log_info "Starting ODS update..."

    local current_version assistant_first_update=false
    local assistant_first_rollback_source=""
    current_version=$(get_current_version)

    if ! ensure_source_checkout_for_update; then
        return 1
    fi
    _load_update_snapshot_contract || return 1

    if is_assistant_first_install; then
        _capture_assistant_first_source_state || return 1
    fi

    # Assistant First resolves, materializes, and assesses an exact candidate in
    # a private disposable repository. Nothing below this point has changed the
    # installed checkout, rollback state, services, images, or extension state.
    if is_assistant_first_install; then
        assistant_first_update=true
        if ! _prepare_assistant_first_update_candidate; then
            return 1
        fi
    fi

    # ── Step 1: rollback snapshot ─────────────────────────────────────────────
    local timestamp
    timestamp=$(date +%Y%m%d-%H%M%S)
    local snap_dir
    if ! snap_dir=$(snapshot_pre_update "$timestamp"); then
        _cleanup_update_candidate
        return 1
    fi

    # Resolve compose flags once — used in restart and rollback paths.
    local compose_flags=""
    compose_flags=$(resolve_compose_flags 2>/dev/null || true)

    # ── Step 2: apply the already assessed source candidate ───────────────────
    cd "$INSTALL_DIR"
    if $assistant_first_update; then
        if ! _verify_assistant_first_source_state; then
            _cleanup_update_candidate
            return 1
        fi
        _arm_assistant_first_update_signal_rollback "$snap_dir" "$compose_flags"
        log_info "Applying exact source candidate ${UPDATE_CANDIDATE_REVISION}..."
        if ! _apply_assistant_first_update_candidate; then
            local failed_candidate_revision failed_head
            failed_candidate_revision="$UPDATE_CANDIDATE_REVISION"
            failed_head=$(git -C "$INSTALL_DIR" rev-parse --verify 'HEAD^{commit}' \
                2>/dev/null || true)
            _cleanup_update_candidate
            if [[ "$failed_head" == "$failed_candidate_revision" ]]; then
                UPDATE_SOURCE_APPLIED_HEAD="$failed_head"
                trap '' HUP INT TERM
                _update_rollback "Exact candidate checkout failed." \
                    "$snap_dir" "$compose_flags" "$UPDATE_SOURCE_ORIGINAL_HEAD"
            elif [[ "$failed_head" != "$UPDATE_SOURCE_ORIGINAL_HEAD" ]]; then
                log_error "Exact candidate checkout failed after unexpected source drift."
                log_info "Refusing to reset an unrecognized revision; manual recovery is required."
            else
                log_error "Exact candidate checkout failed before changing installed source."
                log_info "No service restart or snapshot restore was required."
            fi
            return 1
        fi
        UPDATE_SIGNAL_MUTATION_STARTED=true
        if [[ "$UPDATE_CANDIDATE_REVISION" != "$UPDATE_SOURCE_ORIGINAL_HEAD" ]]; then
            UPDATE_SOURCE_APPLIED_HEAD="$UPDATE_CANDIDATE_REVISION"
            assistant_first_rollback_source="$UPDATE_SOURCE_ORIGINAL_HEAD"
        fi
        _cleanup_update_candidate
    else
        # Preserve the established Full/Core/Custom source-update path.
        log_info "Pulling latest changes..."
        git fetch origin
        if ! git pull origin main && ! git pull origin master; then
            _update_rollback "Git pull failed." "$snap_dir" "$compose_flags"
            return 1
        fi
    fi

    # ── Step 3: migrations ────────────────────────────────────────────────────
    local migrations_dir="${INSTALL_DIR}/migrations"
    if [[ -d "$migrations_dir" ]]; then
        log_info "Running migrations..."
        for migration in "$migrations_dir"/migrate-v*.sh; do
            if [[ -f "$migration" && -x "$migration" ]]; then
                log_info "Running: $(basename "$migration")"
                if ! bash "$migration"; then
                    $assistant_first_update && trap '' HUP INT TERM
                    _update_rollback "Migration failed: $(basename "$migration")." \
                        "$snap_dir" "$compose_flags" \
                        "$assistant_first_rollback_source"
                    return 1
                fi
            fi
        done
    fi

    # ── Step 4: restart services ──────────────────────────────────────────────
    log_info "Restarting services..."
    cd "$INSTALL_DIR"
    if [[ -n "${compose_flags}" ]]; then
        if ! docker compose ${compose_flags} down --remove-orphans; then
            log_warn "docker compose v2 down failed, trying v1..."
            docker-compose ${compose_flags} down --remove-orphans
        fi
        if ! docker compose ${compose_flags} up -d; then
            log_warn "docker compose v2 up failed, trying v1..."
            docker-compose ${compose_flags} up -d
        fi
    elif [[ -f "${INSTALL_DIR}/docker-compose.yml" ]]; then
        if ! docker compose down --remove-orphans; then
            log_warn "docker compose v2 down failed, trying v1..."
            docker-compose down --remove-orphans
        fi
        if ! docker compose up -d; then
            log_warn "docker compose v2 up failed, trying v1..."
            docker-compose up -d
        fi
    else
        log_warn "No compose files found. Skipping container restart."
    fi

    # ── Step 5: health-check with timeout ────────────────────────────────────
    if ! wait_for_healthy; then
        $assistant_first_update && trap '' HUP INT TERM
        _update_rollback \
            "Services failed to become healthy after update (timeout: ${HEALTH_TIMEOUT}s)." \
            "$snap_dir" "$compose_flags" "$assistant_first_rollback_source"
        return 1
    fi

    # ── Step 6: record new version ────────────────────────────────────────────
    local new_version
    new_version=$(git describe --tags 2>/dev/null || git rev-parse --short HEAD)
    local version_data='{}'
    [[ -f "$VERSION_FILE" ]] && version_data=$(cat "$VERSION_FILE")
    local tmp_version_file
    tmp_version_file=$(mktemp "${VERSION_FILE}.tmp.XXXXXX")
    echo "$version_data" | jq \
        --arg v    "$new_version" \
        --arg ts   "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --arg snap "$snap_dir" \
        '.version = $v | .last_update = $ts | .last_rollback_point = $snap' \
        > "$tmp_version_file"
    mv -f "$tmp_version_file" "$VERSION_FILE"

    if $assistant_first_update; then
        _disarm_assistant_first_update_signal_rollback
        _clear_assistant_first_source_state
    fi

    log_ok "Update complete! Version: ${new_version}"
    log_info "Rollback point retained at: ${snap_dir}"
}

#==============================================================================
# COMMAND: ROLLBACK
#==============================================================================

cmd_rollback() {
    local mutation_guard_status=0
    if is_assistant_first_install && ! _assistant_first_mutation_guard_held; then
        _run_with_assistant_first_mutation_guard rollback "$@" \
            || mutation_guard_status=$?
        return "$mutation_guard_status"
    fi

    local target="${1:-}"
    local backup_path=""

    _load_update_snapshot_contract || return 1

    if [[ -n "$target" ]]; then
        # Explicit target: search rollback snapshots first, then general backups.
        for candidate in \
            "${ROLLBACK_DIR}/${target}" \
            "${ROLLBACK_DIR}/pre-update-${target}" \
            "${BACKUP_DIR}/${target}" \
            "${BACKUP_DIR}/backup-${target}"; do
            if [[ -d "$candidate" ]]; then
                backup_path="$candidate"
                break
            fi
        done
    else
        # No target: prefer the most recent pre-update rollback snapshot,
        # fall back to the most recent general backup.
        backup_path=$(find "${ROLLBACK_DIR}" -maxdepth 1 -type d -name "pre-update-*" \
            2>/dev/null | sort -r | head -1)
        if [[ -z "$backup_path" ]]; then
            backup_path=$(find "${BACKUP_DIR}" -maxdepth 1 -type d -name "backup-*" \
                2>/dev/null | sort -r | head -1)
        fi
    fi

    if [[ -z "$backup_path" || ! -d "$backup_path" ]]; then
        log_error "No backup or rollback snapshot found to restore from."
        echo ""
        echo "Pre-update rollback snapshots (${ROLLBACK_DIR}):"
        ls -1 "${ROLLBACK_DIR}" 2>/dev/null | grep '^pre-update-' || echo "  (none)"
        echo ""
        echo "General backups (${BACKUP_DIR}):"
        ls -1 "${BACKUP_DIR}" 2>/dev/null | grep '^backup-' || echo "  (none)"
        return 1
    fi

    log_info "Rolling back from: $(basename "$backup_path")"

    # Show metadata (snapshot.json or legacy metadata.json)
    local meta_file="${backup_path}/snapshot.json"
    [[ -f "$meta_file" ]] || meta_file="${backup_path}/metadata.json"
    if [[ -f "$meta_file" ]]; then
        local bver btime
        bver=$(jq -r '.version  // "unknown"' "$meta_file")
        btime=$(jq -r '.timestamp // "unknown"' "$meta_file")
        log_info "Snapshot version : ${bver}"
        log_info "Snapshot time    : ${btime}"
    fi

    local compose_flags=""
    local -a compose_args=()
    compose_flags=$(resolve_compose_flags 2>/dev/null || true)
    if [[ -n "$compose_flags" ]]; then
        compose_flags_parse "$compose_flags" || {
            log_error "Resolved compose flags are malformed."
            return 1
        }
        compose_args=("${COMPOSE_PARSED_ARGS[@]}")
    fi

    # A pre-update snapshot must be fully validated before stopping services.
    # This prevents malformed or tampered rollback input from mutating runtime.
    if [[ -f "${backup_path}/snapshot.json" ]]; then
        if ! _validate_snapshot "$backup_path"; then
            log_error "Snapshot validation failed before service stop; nothing was changed."
            return 1
        fi
    fi

    # Stop services using the currently active compose stack.
    log_info "Stopping services..."
    cd "$INSTALL_DIR"
    if [[ ${#compose_args[@]} -gt 0 ]]; then
        if ! docker compose "${compose_args[@]}" down; then
            log_warn "docker compose v2 down failed, trying v1..."
            docker-compose "${compose_args[@]}" down
        fi
    else
        if ! docker compose down; then
            log_warn "docker compose v2 down failed, trying v1..."
            docker-compose down
        fi
    fi

    # Restore — use _restore_snapshot for pre-update snapshots (they include
    # config-* dirs); fall back to flat-file copy for legacy general backups.
    if [[ -f "${backup_path}/snapshot.json" ]]; then
        if ! _restore_snapshot "$backup_path"; then
            log_error "Restore failed. Manual recovery required."
            log_error "  Source: ${backup_path}"
            return 1
        fi
    else
        log_info "Restoring configuration files (legacy backup)..."
        shopt -s dotglob
        for file in "$backup_path"/*; do
            if [[ -f "$file" && "$(basename "$file")" != "metadata.json" ]]; then
                cp "$file" "$INSTALL_DIR/"
                log_info "  Restored: $(basename "$file")"
            fi
        done
        shopt -u dotglob
    fi

    # The restored .env and compose files may select a different backend or
    # overlay than the stack that was just stopped.  If the snapshot predates
    # .compose-flags, drop the stale cache so resolution falls back to the
    # restored .env.
    local snapshot_compose_flags="${backup_path}/.compose-flags"
    if [[ -f "${backup_path}/snapshot.json" ]] && \
       [[ "$(jq -r '.schema // "legacy"' "${backup_path}/snapshot.json" | tr -d '\r')" == \
          "${ODS_UPDATE_SNAPSHOT_SCHEMA}" ]]; then
        snapshot_compose_flags="${backup_path}/payload/.compose-flags"
    fi
    if [[ ! -f "$snapshot_compose_flags" && -f "${INSTALL_DIR}/.compose-flags" ]]; then
        log_info "Snapshot has no .compose-flags; clearing stale cached stack."
        rm -f "${INSTALL_DIR}/.compose-flags"
    fi

    # Restart services using the restored compose stack
    log_info "Restarting services..."
    local restored_compose_flags=""
    local -a restored_compose_args=()
    restored_compose_flags=$(resolve_compose_flags 2>/dev/null || true)
    if [[ -n "$restored_compose_flags" ]]; then
        compose_flags_parse "$restored_compose_flags" || {
            log_error "Restored compose flags are malformed."
            return 1
        }
        restored_compose_args=("${COMPOSE_PARSED_ARGS[@]}")
    fi

    if [[ ${#restored_compose_args[@]} -gt 0 ]]; then
        if ! docker compose "${restored_compose_args[@]}" up -d; then
            log_warn "docker compose v2 up failed, trying v1..."
            docker-compose "${restored_compose_args[@]}" up -d
        fi
    else
        if ! docker compose up -d; then
            log_warn "docker compose v2 up failed, trying v1..."
            docker-compose up -d
        fi
    fi

    # Verify health using the same timeout-aware poller as cmd_update
    if wait_for_healthy; then
        log_ok "Rollback complete!"
    else
        log_warn "Rollback complete but health checks failed. Manual intervention may be required."
        return 1
    fi
}

#==============================================================================
# COMMAND: CHANGELOG
#==============================================================================

cmd_changelog() {
    local version="${1:-}"
    
    if [[ -n "$version" ]]; then
        # Fetch specific version from GitHub
        log_info "Fetching changelog for version ${version}..."
        local api_url="https://api.github.com/repos/${GITHUB_REPO}/releases/tags/${version}"
        local response
        if response=$(curl -sf --max-time 15 "${api_url}" 2>/dev/null); then
            echo "$response" | jq -r '.body // "No changelog available."'
        else
            log_error "Could not fetch changelog for ${version}"
            return 1
        fi
    else
        # Show local CHANGELOG.md
        local changelog_file="${INSTALL_DIR}/CHANGELOG.md"
        if [[ -f "$changelog_file" ]]; then
            # Show first 50 lines (most recent entries)
            head -50 "$changelog_file"
        else
            log_warn "No local CHANGELOG.md found."
            log_info "Fetching latest release notes from GitHub..."
            cmd_changelog "$(curl -sf --max-time 15 "https://api.github.com/repos/${GITHUB_REPO}/releases/latest" | jq -r '.tag_name // empty')" || true
        fi
    fi
}

#==============================================================================
# COMMAND: HEALTH
#==============================================================================

cmd_health() {
    log_info "Running health checks..."
    local all_healthy=true
    local timeout_start=$SECONDS
    
    # Check Docker is running
    if ! docker info &>/dev/null; then
        log_error "Docker is not running"
        return 1
    fi
    log_ok "Docker is running"
    
    # Check containers
    cd "$INSTALL_DIR"
    local -a compose_cmd
    if docker compose version &>/dev/null; then
        compose_cmd=(docker compose)
    elif command -v docker-compose >/dev/null 2>&1; then
        compose_cmd=(docker-compose)
    else
        log_error "Docker Compose is not available"
        return 1
    fi

    local compose_flags=""
    local -a compose_args=()
    compose_flags=$(resolve_compose_flags 2>/dev/null || true)
    if [[ -n "$compose_flags" ]]; then
        compose_flags_parse "$compose_flags" || {
            log_error "Resolved compose flags are malformed."
            return 1
        }
        compose_args=("${COMPOSE_PARSED_ARGS[@]}")
    fi
    
    local services
    services=$("${compose_cmd[@]}" "${compose_args[@]}" ps --services 2>/dev/null || echo "")
    
    if [[ -z "$services" ]]; then
        if [[ -n "$compose_flags" ]]; then
            log_warn "No services found for resolved compose stack: ${compose_flags}"
        else
            log_warn "No compose stack could be resolved for this install"
        fi
        return 1
    fi
    
    for service in $services; do
        local status
        status=$("${compose_cmd[@]}" "${compose_args[@]}" ps --format json "$service" 2>/dev/null \
            | jq -r 'if type == "array" then (.[0].State // "unknown") else (.State // "unknown") end' 2>/dev/null \
            || echo "unknown")
        
        if [[ "$status" == "running" ]]; then
            log_ok "Service ${service}: running"
        else
            log_error "Service ${service}: ${status}"
            all_healthy=false
        fi
    done
    
    # Check dashboard API health endpoint
    local dashboard_api_port="${DASHBOARD_API_PORT:-}"
    [[ -n "$dashboard_api_port" ]] || dashboard_api_port="$(env_file_value DASHBOARD_API_PORT)"
    dashboard_api_port="${dashboard_api_port:-3002}"
    if curl -sf --max-time 15 "http://127.0.0.1:${dashboard_api_port}/health" &>/dev/null; then
        log_ok "Dashboard API: healthy"
    elif curl -sf --max-time 15 "http://127.0.0.1:${dashboard_api_port}/api/status" &>/dev/null; then
        log_ok "Dashboard API: responding"
    else
        log_warn "Dashboard API: not responding on port ${dashboard_api_port}"
    fi
    
    # Check llama-server health
    local llama_server_port="${OLLAMA_PORT:-${LLAMA_SERVER_PORT:-}}"
    [[ -n "$llama_server_port" ]] || llama_server_port="$(env_file_value OLLAMA_PORT)"
    [[ -n "$llama_server_port" ]] || llama_server_port="$(env_file_value LLAMA_SERVER_PORT)"
    llama_server_port="${llama_server_port:-8080}"
    if curl -sf --max-time 15 "http://127.0.0.1:${llama_server_port}/v1/models" &>/dev/null; then
        log_ok "llama-server: healthy"
    else
        log_warn "llama-server: not responding on port ${llama_server_port}"
    fi
    
    if $all_healthy; then
        log_ok "All health checks passed"
        return 0
    else
        log_error "Some health checks failed"
        return 1
    fi
}

#==============================================================================
# USAGE
#==============================================================================

usage() {
    cat << EOF
ODS Update Manager

Usage: ods-update.sh <command> [options]

Commands:
  check          Check for available updates
  status         Show current version, update status, and rollback info
  backup [name]  Create a named general backup of current configuration
  snapshot [ts]  Create an integrity-checked pre-update rollback snapshot
                 (optional timestamp format: YYYYMMDD-HHMMSS)
  update         Source-checkout only: update source, run migrations, restart,
                 health-check, and auto-restore on failure. Assistant First
                 verifies an exact candidate before snapshot or checkout.
  rollback [id]  Restore from a rollback snapshot or general backup
                 (default: most recent pre-update snapshot)
  changelog [v]  Show changelog (optional: specific version)
  health         Run health checks on all services

Rollback snapshots:
  Stored in:  <install_dir>/data/backups/pre-update-<timestamp>/
  Contents:   exact environment/Compose state, config/, extension lockfile,
              transaction journals/receipts, and data/user-extensions/
  Security:   owner-private, checksum-verified; secret custody stays in place
  Retained:   MAX_BACKUPS most recent snapshots (oldest pruned automatically)

Environment Variables:
  GITHUB_TOKEN        GitHub API token (for higher rate limits)
  UPDATE_CHANNEL      stable|beta|nightly (default: stable)
  MAX_BACKUPS         Number of snapshots/backups to retain (default: 10)
  HEALTH_TIMEOUT      Seconds to wait for healthy services (default: 120)
  ODS_MUTATION_GUARD_TIMEOUT
                      Seconds to wait for an Assistant First lifecycle mutation
                      before failing fast (default: 5)
  DASHBOARD_API_PORT  Dashboard API port (default: 3002)
  OLLAMA_PORT         llama-server port (default: 8080)

Examples:
  ods-update.sh check
  ods-update.sh status
  ods-update.sh backup pre-experiment
  ods-update.sh snapshot
  ods update                    # normal runtime/image update
  ods-update.sh update          # source checkout only
  ods-update.sh rollback
  ods-update.sh rollback 20260317-120000
  ods-update.sh changelog v1.1.0
  ods-update.sh health

EOF
}

#==============================================================================
# MAIN
#==============================================================================

main() {
    local command="${1:-help}"
    shift || true

    case "$command" in
        check)
            cmd_check "$@"
            ;;
        status)
            cmd_status "$@"
            ;;
        backup)
            cmd_backup "$@"
            ;;
        snapshot)
            cmd_snapshot "$@"
            ;;
        update)
            cmd_update "$@"
            ;;
        rollback)
            cmd_rollback "$@"
            ;;
        changelog)
            cmd_changelog "$@"
            ;;
        health)
            cmd_health "$@"
            ;;
        help|--help|-h)
            usage
            ;;
        *)
            log_error "Unknown command: $command"
            echo ""
            usage
            exit 1
            ;;
    esac
}

main "$@"
