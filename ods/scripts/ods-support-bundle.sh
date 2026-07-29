#!/usr/bin/env bash
set -euo pipefail

TOOL_VERSION="1"
REDACTION_VERSION="1"
DEFAULT_LOG_TAIL=200
MAX_LOG_CONTAINERS=25

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

OUTPUT_DIR="${ROOT_DIR}/artifacts/support"
JSON_OUTPUT=false
INCLUDE_LOGS=true
DOCKER_BIN="${ODS_SUPPORT_BUNDLE_DOCKER:-docker}"

usage() {
    cat <<'EOF'
Usage: scripts/ods-support-bundle.sh [OPTIONS]

Create a redacted diagnostics bundle for ODS support.

Options:
  --output DIR   Write bundle directory/archive under DIR
  --json         Print machine-readable result JSON
  --no-logs      Skip Docker container log collection
  -h, --help     Show this help

The generated archive is safe-by-default, but review it before posting to a
public issue. Raw .env is never included; only config/env.redacted is written.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --output)
            OUTPUT_DIR="${2:-}"
            [[ -n "$OUTPUT_DIR" ]] || { echo "ERROR: --output requires a directory" >&2; exit 2; }
            shift 2
            ;;
        --json)
            JSON_OUTPUT=true
            shift
            ;;
        --no-logs)
            INCLUDE_LOGS=false
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

detect_python() {
    if command -v python3 >/dev/null 2>&1; then
        command -v python3
    elif command -v python >/dev/null 2>&1; then
        command -v python
    else
        return 1
    fi
}

detect_bash() {
    local candidate
    for candidate in \
        "${ODS_SUPPORT_BUNDLE_BASH:-}" \
        /opt/homebrew/bin/bash \
        /usr/local/bin/bash \
        "$(command -v bash 2>/dev/null || true)" \
        /bin/bash
    do
        [[ -n "$candidate" && -x "$candidate" ]] || continue
        if "$candidate" -c '[[ ${BASH_VERSINFO[0]:-0} -ge 4 ]]' >/dev/null 2>&1; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    command -v bash 2>/dev/null || printf '%s\n' /bin/bash
}

PYTHON_CMD="$(detect_python)" || {
    echo "ERROR: python3 or python is required to build a redacted support bundle" >&2
    exit 1
}
BASH_CMD="$(detect_bash)"

mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
BUNDLE_NAME="ods-support-${timestamp}-$$"
BUNDLE_DIR="${OUTPUT_DIR%/}/${BUNDLE_NAME}"
ARCHIVE_PATH="${BUNDLE_DIR}.tar.gz"
STATUS_FILE="${BUNDLE_DIR}/manifest/command-status.tsv"

mkdir -p \
    "$BUNDLE_DIR/config" \
    "$BUNDLE_DIR/diagnostics" \
    "$BUNDLE_DIR/docker" \
    "$BUNDLE_DIR/logs" \
    "$BUNDLE_DIR/manifest" \
    "$BUNDLE_DIR/system" \
    "$BUNDLE_DIR/validation"

shell_quote() {
    printf "%q" "$1"
}

record_command() {
    local label="$1"
    local rel_path="$2"
    local exit_code="$3"
    printf '%s\t%s\t%s\n' "$label" "$rel_path" "$exit_code" >> "$STATUS_FILE"
}

redact_file() {
    local file="$1"
    [[ -f "$file" ]] || return 0

    "$PYTHON_CMD" - "$file" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    text = path.read_text(encoding="utf-8", errors="replace")
except OSError:
    raise SystemExit(0)

secret_word = r"(?:KEY|TOKEN|SECRET|PASSWORD|PASS|SALT|AUTH|CREDENTIAL)"

patterns = [
    (re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)((?:authorization|x-api-key|api-key|apikey)\s*[:=]\s*)([\"']?)[^\"'\s,}]+"), r"\1\2[REDACTED]"),
    (re.compile(r"(?i)(https?://)([^/\s:@]+):([^@\s/]+)@"), r"\1[REDACTED]@"),
    (
        re.compile(rf"(?im)^([ \t]*(?:export[ \t]+)?[A-Za-z_][A-Za-z0-9_]*{secret_word}[A-Za-z0-9_]*[ \t]*=[ \t]*).*$"),
        r"\1[REDACTED]",
    ),
    (
        re.compile(rf"(?im)(^|[{{,]\s*)([\"']?[A-Za-z0-9_-]*{secret_word}[A-Za-z0-9_-]*[\"']?\s*:\s*)([\"']?)[^\"'\s,\n}}{{\[]+([\"']?)"),
        r'\1\2"[REDACTED]"',
    ),
]

for pattern, replacement in patterns:
    text = pattern.sub(replacement, text)

path.write_text(text, encoding="utf-8")
PY
}

collect_shell() {
    local rel_path="$1"
    local label="$2"
    local command="$3"
    local abs_path="${BUNDLE_DIR}/${rel_path}"
    local exit_code

    mkdir -p "$(dirname "$abs_path")"
    set +e
    (
        cd "$ROOT_DIR" || exit 1
        "$BASH_CMD" -lc "$command"
    ) > "$abs_path" 2>&1
    exit_code=$?
    set -e

    redact_file "$abs_path"
    record_command "$label" "$rel_path" "$exit_code"
    return 0
}

write_file() {
    local rel_path="$1"
    local abs_path="${BUNDLE_DIR}/${rel_path}"
    mkdir -p "$(dirname "$abs_path")"
    cat > "$abs_path"
    redact_file "$abs_path"
}

copy_if_exists() {
    local src="$1"
    local rel_path="$2"
    local abs_path="${BUNDLE_DIR}/${rel_path}"
    if [[ -f "$ROOT_DIR/$src" ]]; then
        mkdir -p "$(dirname "$abs_path")"
        cp "$ROOT_DIR/$src" "$abs_path"
        redact_file "$abs_path"
    fi
}

write_redacted_env() {
    local env_path="$ROOT_DIR/.env"
    local out_path="$BUNDLE_DIR/config/env.redacted"

    if [[ ! -f "$env_path" ]]; then
        printf 'No .env file found at %s\n' "$env_path" > "$out_path"
        return 0
    fi

    "$PYTHON_CMD" - "$env_path" "$out_path" <<'PY'
import re
import sys
from pathlib import Path

src = Path(sys.argv[1])
dest = Path(sys.argv[2])
# USER|EMAIL|BEARER cover schema secret:true keys the old pattern missed —
# N8N_USER, LANGFUSE_INIT_USER_EMAIL, LANGFUSE_MINIO_ROOT_USER — which are
# published in cleartext when this env.redacted is shared on a public issue.
# Match .env.schema.json's secret set / the CLI's config-show masking.
secret = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASS|SALT|AUTH|CREDENTIAL|USER|EMAIL|BEARER)", re.I)

lines = []
for line in src.read_text(encoding="utf-8", errors="replace").splitlines():
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in line:
        lines.append(line)
        continue
    prefix, _value = line.split("=", 1)
    key = prefix.strip()
    if key.startswith("export "):
        key = key[7:].strip()
    if secret.search(key):
        lines.append(f"{prefix}=[REDACTED]")
    else:
        lines.append(line)

dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
    redact_file "$out_path"
}

read_env_value() {
    local key="$1"
    local default="$2"
    local env_path="$ROOT_DIR/.env"

    if [[ ! -f "$env_path" ]]; then
        printf '%s\n' "$default"
        return 0
    fi

    "$PYTHON_CMD" - "$env_path" "$key" "$default" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
target = sys.argv[2]
default = sys.argv[3]

for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    key = key.strip()
    if key.startswith("export "):
        key = key[7:].strip()
    if key == target:
        print(value.strip().strip('"').strip("'"))
        break
else:
    print(default)
PY
}

docker_cli_available() {
    [[ "${ODS_SUPPORT_BUNDLE_DISABLE_DOCKER:-}" == "1" ]] && return 1
    command -v "$DOCKER_BIN" >/dev/null 2>&1
}

docker_daemon_available() {
    docker_cli_available || return 1
    if command -v timeout >/dev/null 2>&1; then
        timeout 10 "$DOCKER_BIN" info >/dev/null 2>&1
    else
        "$DOCKER_BIN" info >/dev/null 2>&1
    fi
}

docker_compose_available() {
    docker_cli_available && "$DOCKER_BIN" compose version >/dev/null 2>&1
}

safe_filename() {
    "$PYTHON_CMD" - "$1" <<'PY'
import re
import sys
name = re.sub(r"[^A-Za-z0-9_.-]+", "_", sys.argv[1]).strip("._")
print(name or "container")
PY
}

collect_system_info() {
    write_file "system/bash.txt" <<EOF
selected_bash=$BASH_CMD
selected_bash_version=$("$BASH_CMD" -c 'printf "%s\n" "${BASH_VERSION:-unknown}"' 2>/dev/null || printf 'unknown\n')
EOF
    collect_shell "system/platform.txt" "platform-summary" '
        printf "generated_at_utc="; date -u +"%Y-%m-%dT%H:%M:%SZ"
        printf "root_dir=%s\n" "$PWD"
        uname -a 2>/dev/null || true
        if [[ -r /proc/sys/kernel/osrelease ]] && grep -qi microsoft /proc/sys/kernel/osrelease; then
            echo "wsl=true"
        elif [[ -r /proc/version ]] && grep -qi microsoft /proc/version; then
            echo "wsl=true"
        else
            echo "wsl=false"
        fi
        if [[ -f /etc/os-release ]]; then
            echo ""
            cat /etc/os-release
        fi
    '
    collect_shell "system/resources.txt" "resource-summary" '
        echo "Disk:"
        df -h . "$HOME" 2>/dev/null || df -h . 2>/dev/null || true
        echo ""
        echo "Memory:"
        if command -v free >/dev/null 2>&1; then
            free -h
        elif [[ -f /proc/meminfo ]]; then
            head -20 /proc/meminfo
        else
            vm_stat 2>/dev/null || true
        fi
    '
    collect_shell "system/listening-ports.txt" "listening-ports" '
        if command -v ss >/dev/null 2>&1; then
            ss -ltnp 2>/dev/null || ss -ltn 2>/dev/null || true
        elif command -v lsof >/dev/null 2>&1; then
            lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null || true
        elif command -v netstat >/dev/null 2>&1; then
            netstat -an 2>/dev/null | grep LISTEN || true
        else
            echo "No supported port listing tool found"
        fi
    '
    collect_shell "system/git.txt" "git-summary" '
        git rev-parse --show-toplevel 2>/dev/null || true
        git status --short --branch 2>/dev/null || true
        git log --oneline --decorate -5 2>/dev/null || true
        git remote -v 2>/dev/null || true
    '
}

collect_config() {
    copy_if_exists "manifest.json" "config/manifest.json"
    copy_if_exists ".env.schema.json" "config/env.schema.json"
    copy_if_exists ".env.example" "config/env.example"
    write_redacted_env
}

collect_diagnostics() {
    if [[ -f "$ROOT_DIR/scripts/ods-doctor.sh" ]]; then
        collect_shell "diagnostics/ods-doctor.log" "ods-doctor" "$(shell_quote "$BASH_CMD") $(shell_quote "$ROOT_DIR/scripts/ods-doctor.sh") $(shell_quote "$BUNDLE_DIR/diagnostics/ods-doctor.json")"
        redact_file "$BUNDLE_DIR/diagnostics/ods-doctor.json"
    else
        write_file "diagnostics/ods-doctor.log" <<< "scripts/ods-doctor.sh not found"
        record_command "ods-doctor" "diagnostics/ods-doctor.log" "127"
    fi

    if [[ -f "$ROOT_DIR/scripts/audit-extensions.py" ]]; then
        collect_shell "diagnostics/extension-audit.json" "extension-audit" "$(shell_quote "$PYTHON_CMD") $(shell_quote "$ROOT_DIR/scripts/audit-extensions.py") --project-dir $(shell_quote "$ROOT_DIR") --json"
    else
        write_file "diagnostics/extension-audit.json" <<< '{"error":"scripts/audit-extensions.py not found"}'
        record_command "extension-audit" "diagnostics/extension-audit.json" "127"
    fi
}

collect_compose_validation() {
    local gpu_backend
    local tier
    local gpu_count
    local ods_mode
    local lemonade_external
    local amd_runtime
    local amd_managed
    local flags_file="$BUNDLE_DIR/validation/compose-flags.txt"
    local flags_err="$BUNDLE_DIR/validation/compose-flags.err"
    local flags
    local resolve_exit

    gpu_backend="$(read_env_value GPU_BACKEND nvidia)"
    tier="$(read_env_value TIER 1)"
    gpu_count="$(read_env_value GPU_COUNT 1)"
    ods_mode="$(read_env_value ODS_MODE local)"
    lemonade_external="$(read_env_value LEMONADE_EXTERNAL false)"
    amd_runtime="$(read_env_value AMD_INFERENCE_RUNTIME "")"
    amd_managed="$(read_env_value AMD_INFERENCE_MANAGED "")"

    if [[ ! -f "$ROOT_DIR/scripts/resolve-compose-stack.sh" ]]; then
        write_file "validation/compose-config.txt" <<< "scripts/resolve-compose-stack.sh not found"
        record_command "resolve-compose-stack" "validation/compose-config.txt" "127"
        return 0
    fi

    set +e
    flags="$(
        cd "$ROOT_DIR" && \
        LEMONADE_EXTERNAL="$lemonade_external" \
        AMD_INFERENCE_RUNTIME="$amd_runtime" \
        AMD_INFERENCE_MANAGED="$amd_managed" \
        "$BASH_CMD" scripts/resolve-compose-stack.sh \
            --script-dir "$ROOT_DIR" \
            --tier "$tier" \
            --gpu-backend "$gpu_backend" \
            --gpu-count "$gpu_count" \
            --ods-mode "$ods_mode" \
            --skip-broken \
            2> "$flags_err"
    )"
    resolve_exit=$?
    set -e

    {
        printf 'GPU_BACKEND=%s\n' "$gpu_backend"
        printf 'TIER=%s\n' "$tier"
        printf 'GPU_COUNT=%s\n' "$gpu_count"
        printf 'ODS_MODE=%s\n' "$ods_mode"
        printf 'LEMONADE_EXTERNAL=%s\n' "$lemonade_external"
        printf 'AMD_INFERENCE_RUNTIME=%s\n' "$amd_runtime"
        printf 'AMD_INFERENCE_MANAGED=%s\n' "$amd_managed"
        printf 'COMPOSE_FLAGS=%s\n' "$flags"
        if [[ -s "$flags_err" ]]; then
            echo ""
            echo "stderr:"
            cat "$flags_err"
        fi
    } > "$flags_file"
    redact_file "$flags_file"
    redact_file "$flags_err"
    record_command "resolve-compose-stack" "validation/compose-flags.txt" "$resolve_exit"

    if [[ "$resolve_exit" -ne 0 ]]; then
        write_file "validation/compose-config.txt" <<< "Compose validation skipped because compose flag resolution failed"
        record_command "compose-config" "validation/compose-config.txt" "127"
        return 0
    fi

    if [[ "${ODS_SUPPORT_BUNDLE_DISABLE_DOCKER:-}" == "1" ]] || { ! docker_compose_available && ! command -v docker-compose >/dev/null 2>&1; }; then
        write_file "validation/compose-config.txt" <<< "Compose validation skipped because docker compose is not available"
        record_command "compose-config" "validation/compose-config.txt" "127"
        return 0
    fi

    local cmd
    cmd="$(shell_quote "$BASH_CMD") $(shell_quote "$ROOT_DIR/scripts/validate-compose-stack.sh") --compose-flags $(shell_quote "$flags")"
    if [[ -f "$ROOT_DIR/.env" ]]; then
        cmd="${cmd} --env-file $(shell_quote "$ROOT_DIR/.env")"
    fi
    collect_shell "validation/compose-config.txt" "compose-config" "$cmd"
}

collect_docker() {
    if ! docker_cli_available; then
        write_file "docker/unavailable.txt" <<< "Docker CLI not available or disabled"
        record_command "docker-version" "docker/unavailable.txt" "127"
        return 0
    fi

    collect_shell "docker/version.txt" "docker-version" "$(shell_quote "$DOCKER_BIN") version"

    if ! docker_daemon_available; then
        write_file "docker/info.txt" <<< "Docker daemon is not reachable"
        record_command "docker-info" "docker/info.txt" "1"
        return 0
    fi

    collect_shell "docker/info.txt" "docker-info" "$(shell_quote "$DOCKER_BIN") info"
    collect_shell "docker/ps.txt" "docker-ps" "$(shell_quote "$DOCKER_BIN") ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'"

    if [[ "$INCLUDE_LOGS" != "true" ]]; then
        write_file "logs/skipped.txt" <<< "Docker log collection skipped by --no-logs"
        return 0
    fi

    local names_file="$BUNDLE_DIR/docker/container-names.txt"
    local names_exit
    set +e
    "$DOCKER_BIN" ps --format '{{.Names}}' > "$names_file" 2>&1
    names_exit=$?
    set -e
    redact_file "$names_file"
    record_command "docker-container-names" "docker/container-names.txt" "$names_exit"
    [[ "$names_exit" -eq 0 ]] || return 0

    local count=0
    local container
    while IFS= read -r container; do
        [[ -n "$container" ]] || continue
        case "$container" in
            ods-*|*ods*)
                ;;
            *)
                continue
                ;;
        esac
        count=$((count + 1))
        [[ "$count" -le "$MAX_LOG_CONTAINERS" ]] || break
        local safe
        safe="$(safe_filename "$container")"
        collect_shell "logs/${safe}.log" "docker-logs:${container}" "$(shell_quote "$DOCKER_BIN") logs --tail ${DEFAULT_LOG_TAIL} $(shell_quote "$container")"
    done < "$names_file"

    if [[ "$count" -eq 0 ]]; then
        write_file "logs/no-ods-containers.txt" <<< "No running ODS-like containers found"
    fi
}

write_manifest() {
    "$PYTHON_CMD" - "$BUNDLE_DIR" "$ARCHIVE_PATH" "$TOOL_VERSION" "$REDACTION_VERSION" "$INCLUDE_LOGS" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

bundle_dir = Path(sys.argv[1])
archive_path = sys.argv[2]
tool_version = sys.argv[3]
redaction_version = sys.argv[4]
include_logs = sys.argv[5].lower() == "true"
status_path = bundle_dir / "manifest" / "command-status.tsv"

commands = []
if status_path.exists():
    for line in status_path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        label, path, exit_code = parts
        try:
            exit_code_value = int(exit_code)
        except ValueError:
            exit_code_value = None
        commands.append({"label": label, "path": path, "exit_code": exit_code_value})

files = []
for path in sorted(bundle_dir.rglob("*")):
    if not path.is_file():
        continue
    rel = path.relative_to(bundle_dir).as_posix()
    if rel == "manifest.json":
        continue
    try:
        size = path.stat().st_size
    except OSError:
        size = None
    files.append({"path": rel, "size_bytes": size})

manifest = {
    "tool": "ods-support-bundle",
    "tool_version": tool_version,
    "redaction_version": redaction_version,
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "archive_path": archive_path,
    "logs_included": include_logs,
    "files": files,
    "commands": commands,
}

(bundle_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
PY
}

write_evidence() {
    "$PYTHON_CMD" - "$BUNDLE_DIR" "$ROOT_DIR" <<'PY'
import hashlib
import json
import platform
import re
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

bundle_dir = Path(sys.argv[1])
root_dir = Path(sys.argv[2])
status_path = bundle_dir / "manifest" / "command-status.tsv"

# Keep in sync with write_redacted_env's key set. USER|EMAIL|BEARER cover
# schema secret:true keys (N8N_USER, LANGFUSE_INIT_USER_EMAIL,
# LANGFUSE_MINIO_ROOT_USER) whose VALUES would otherwise land in evidence.json.
secret = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASS|SALT|AUTH|CREDENTIAL|USER|EMAIL|BEARER)", re.I)


def load_json(path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def env_pairs(path):
    result = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        result[key] = value.strip().strip('"').strip("'")
    return result


def sha256_file(path):
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def command_statuses():
    commands = []
    if status_path.exists():
        for line in status_path.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            label, path, exit_code = parts
            try:
                exit_code_value = int(exit_code)
            except ValueError:
                exit_code_value = None
            commands.append({"label": label, "path": path, "exit_code": exit_code_value})
    return commands


def is_wsl():
    for candidate in (Path("/proc/sys/kernel/osrelease"), Path("/proc/version")):
        try:
            if "microsoft" in candidate.read_text(encoding="utf-8", errors="replace").lower():
                return True
        except OSError:
            continue
    return False


def parse_compose_flags():
    path = bundle_dir / "validation" / "compose-flags.txt"
    result = {"raw": "", "files": []}
    if not path.exists():
        return result
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        if line.startswith("COMPOSE_FLAGS="):
            raw = line.split("=", 1)[1].strip()
            result["raw"] = raw
            try:
                parts = shlex.split(raw)
            except ValueError:
                parts = raw.split()
            files = []
            idx = 0
            while idx < len(parts):
                part = parts[idx]
                if part in {"-f", "--file"} and idx + 1 < len(parts):
                    files.append(parts[idx + 1])
                    idx += 2
                    continue
                if part.startswith("--file="):
                    files.append(part.split("=", 1)[1])
                idx += 1
            result["files"] = files
            break
    return result


manifest = load_json(root_dir / "manifest.json") or {}
doctor = load_json(bundle_dir / "diagnostics" / "ods-doctor.json") or {}
extension_audit = load_json(bundle_dir / "diagnostics" / "extension-audit.json") or {}
env = env_pairs(root_dir / ".env")

public_env_keys = {
    key: {
        "present": True,
        "redacted": bool(secret.search(key)),
        "value": None if secret.search(key) else value,
    }
    for key, value in sorted(env.items())
}

config_hash_targets = [
    "manifest.json",
    ".env.schema.json",
    "config/ports.json",
    "config/golden-paths.json",
    "config/generated-config-contracts.json",
    "config/litellm/lemonade.yaml",
    "extensions/services/hermes/cli-config.yaml.template",
]

evidence = {
    "version": "1",
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "tool": "ods-support-bundle",
    "platform": {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "wsl": is_wsl(),
    },
    "ods": {
        "version": manifest.get("ods_version") or manifest.get("release", {}).get("version"),
        "release": manifest.get("release", {}),
    },
    "backend": {
        "ods_mode": env.get("ODS_MODE"),
        "gpu_backend": env.get("GPU_BACKEND"),
        "gpu_count": env.get("GPU_COUNT"),
        "llm_backend": env.get("LLM_BACKEND"),
        "llm_model": env.get("LLM_MODEL"),
    },
    "inference_contract": doctor.get("runtime", {}).get("inference_contract", {}),
    "env_keys": public_env_keys,
    "compose": parse_compose_flags(),
    "doctor_summary": doctor.get("summary", {}),
    "extension_audit_summary": extension_audit.get("summary", {}),
    "config_hashes": {
        target: sha256_file(root_dir / target)
        for target in config_hash_targets
        if (root_dir / target).exists()
    },
    "commands": command_statuses(),
}

(bundle_dir / "manifest" / "evidence.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
    redact_file "$BUNDLE_DIR/manifest/evidence.json"
}

write_summary_json() {
    "$PYTHON_CMD" - "$BUNDLE_DIR" "$ARCHIVE_PATH" <<'PY'
import json
import sys
from pathlib import Path

bundle_dir = Path(sys.argv[1])
archive_path = Path(sys.argv[2])
manifest = bundle_dir / "manifest.json"
payload = {
    "bundle_dir": bundle_dir.as_posix(),
    "archive": archive_path.as_posix(),
    "manifest": manifest.as_posix(),
    "archive_exists": archive_path.exists(),
    "archive_size_bytes": archive_path.stat().st_size if archive_path.exists() else None,
}
print(json.dumps(payload, indent=2))
PY
}

collect_system_info
collect_config
collect_diagnostics
collect_compose_validation
collect_docker
write_evidence
write_manifest

tar -czf "$ARCHIVE_PATH" -C "$OUTPUT_DIR" "$BUNDLE_NAME"

if [[ "$JSON_OUTPUT" == "true" ]]; then
    write_summary_json
else
    echo "ODS support bundle created:"
    echo "  Directory: $BUNDLE_DIR"
    echo "  Archive:   $ARCHIVE_PATH"
    echo ""
    echo "Review the archive before sharing it publicly."
fi
