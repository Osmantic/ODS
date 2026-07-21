#!/bin/bash
# Validate .env against .env.schema.json
#
# Senior-grade validation goals:
#  - Correctly parse .env files including quotes and "export KEY=..." lines
#  - Report line numbers and actionable messages
#  - Validate required keys, unknown keys, types, enums, numeric ranges, and
#    cross-service runtime contracts
#  - Fail deterministically with a single exit code for CI

# Require Bash 4+ (associative arrays used below).
# macOS ships Bash 3.2 due to licensing; the system /bin/bash will crash on
# `declare -A`. When launched via ods-cli this is invoked through "$BASH",
# but this guard protects direct invocations (e.g. /bin/bash validate-env.sh).
if (( BASH_VERSINFO[0] < 4 )); then
    echo "✗ validate-env.sh requires Bash 4+ (you have ${BASH_VERSION})" >&2
    echo "  macOS ships Bash 3.2 — install a modern Bash:" >&2
    echo "    brew install bash" >&2
    echo "  Then re-run with the Homebrew bash, e.g.:" >&2
    echo "    /opt/homebrew/bin/bash $0 $*" >&2
    exit 1
fi

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${INSTALL_DIR:-$(dirname "$SCRIPT_DIR")}"
ENV_FILE="${1:-${INSTALL_DIR}/.env}"
SCHEMA_FILE="${2:-${INSTALL_DIR}/.env.schema.json}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

usage() {
  cat <<EOF
Usage: $(basename "$0") [ENV_FILE] [SCHEMA_FILE]

Validates a ODS .env file against the JSON schema.

Exit codes:
  0  valid
  2  validation errors
  3  missing deps / unreadable input

Tips:
  - Use .env.example as a reference
  - Quote values containing spaces/special characters
EOF
}

for arg in "$@"; do
  case "$arg" in
    --help|-h) usage; exit 0 ;;
  esac
done

if [[ ! -f "$ENV_FILE" ]]; then
    log_error "Env file not found: $ENV_FILE"
    exit 3
fi

if [[ ! -f "$SCHEMA_FILE" ]]; then
    log_error "Schema file not found: $SCHEMA_FILE"
    exit 3
fi

if ! command -v jq >/dev/null 2>&1; then
    log_error "jq is required for schema validation"
    log_info "Install: sudo apt-get install -y jq  (or your distro equivalent)"
    exit 3
fi

# -----------------------------
# .env parsing (robust)
# -----------------------------
# We intentionally do NOT 'source' the .env for security reasons.
# Instead we parse key/value pairs ourselves.

declare -A ENV_MAP
declare -A ENV_LINE
declare -A ENV_DUPLICATE_FROM

trim() {
  local s="$1"
  s="${s#${s%%[![:space:]]*}}"
  s="${s%${s##*[![:space:]]}}"
  printf '%s' "$s"
}

unquote() {
  # Remove matching single or double quotes; keep inner content as-is.
  local s="$1"
  if [[ ${#s} -ge 2 ]]; then
    if [[ "$s" == "\""*"\"" ]]; then
      printf '%s' "${s:1:${#s}-2}"
      return 0
    fi
    if [[ "$s" == "'"*"'" ]]; then
      printf '%s' "${s:1:${#s}-2}"
      return 0
    fi
  fi
  printf '%s' "$s"
}

# Split KEY=VALUE where VALUE may contain '='
split_kv() {
  local line="$1"
  local key="${line%%=*}"
  local value="${line#*=}"
  key="$(trim "$key")"
  value="$(trim "$value")"
  printf '%s\n' "$key" "$value"
}

line_no=0
while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
  line_no=$((line_no + 1))

  # Strip leading/trailing whitespace
  line="$(trim "$raw_line")"

  # Skip blanks/comments
  [[ -z "$line" ]] && continue
  [[ "$line" =~ ^# ]] && continue

  # Allow: export KEY=VALUE
  if [[ "$line" =~ ^export[[:space:]]+ ]]; then
    line="$(trim "${line#export}")"
  fi

  # Must contain '='
  if [[ "$line" != *"="* ]]; then
    log_warn "Ignoring line $line_no (not KEY=VALUE): $raw_line"
    continue
  fi

  key="$(trim "${line%%=*}")"
  value="$(trim "${line#*=}")"

  if [[ ! "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
    log_warn "Ignoring line $line_no (invalid key '$key')"
    continue
  fi

  # Remove inline comments only when value is unquoted.
  # Example: FOO=bar # comment
  # Keep hashes inside quotes.
  if [[ "$value" != "\""* && "$value" != "'"* ]]; then
    value="$(trim "${value%%#*}")"
  fi

  value="$(trim "$value")"
  value="$(unquote "$value")"

  # Duplicate keys are almost always accidental in generated/merged .env files.
  # Keep the latest value for compatibility, but report duplicates as errors.
  if [[ -n "${ENV_MAP[$key]+x}" ]]; then
    ENV_DUPLICATE_FROM["$key"]="${ENV_LINE[$key]:-?}:$line_no"
  fi

  ENV_MAP["$key"]="$value"
  ENV_LINE["$key"]="$line_no"
done < "$ENV_FILE"

# -----------------------------
# Schema prep
# -----------------------------

missing=()
unknown=()
type_errors=()
enum_errors=()
range_errors=()
length_errors=()
contract_errors=()
duplicate_errors=()

mapfile -t required_keys < <(jq -r '.required[]?' "$SCHEMA_FILE")

mapfile -t schema_keys < <(jq -r '.properties | keys[]' "$SCHEMA_FILE")
declare -A SCHEMA_KEY_SET
for key in "${schema_keys[@]}"; do
    SCHEMA_KEY_SET["$key"]=1
done

# -----------------------------
# Required keys
# -----------------------------

for key in "${required_keys[@]}"; do
    val="${ENV_MAP[$key]-}"
    if [[ -z "$val" ]]; then
        missing+=("$key")
    fi
done

# -----------------------------
# Unknown keys
# -----------------------------

for key in "${!ENV_MAP[@]}"; do
    if [[ -z "${SCHEMA_KEY_SET[$key]-}" ]]; then
        unknown+=("$key")
    fi
done

# -----------------------------
# Type + enum + range checks
# -----------------------------

for key in "${schema_keys[@]}"; do
    val="${ENV_MAP[$key]-}"
    [[ -z "$val" ]] && continue

    expected_type="$(jq -r --arg k "$key" '.properties[$k].type // "string"' "$SCHEMA_FILE")"

    # Type validation
    case "$expected_type" in
        integer)
            if [[ ! "$val" =~ ^-?[0-9]+$ ]]; then
                type_errors+=("$key: expected integer, got '$val' (line ${ENV_LINE[$key]:-?})")
                continue
            fi
            ;;
        number)
            if [[ ! "$val" =~ ^-?[0-9]+([.][0-9]+)?$ ]]; then
                type_errors+=("$key: expected number, got '$val' (line ${ENV_LINE[$key]:-?})")
                continue
            fi
            ;;
        boolean)
            if [[ "$val" != "true" && "$val" != "false" ]]; then
                type_errors+=("$key: expected boolean true/false, got '$val' (line ${ENV_LINE[$key]:-?})")
                continue
            fi
            ;;
    esac

    # Enum validation
    if jq -e --arg k "$key" '.properties[$k].enum? != null' "$SCHEMA_FILE" >/dev/null 2>&1; then
      if [[ "$expected_type" != "string" ]]; then
        : # enums in our schema are for strings; ignore otherwise
      else
        if ! jq -e --arg k "$key" --arg v "$val" '.properties[$k].enum | index($v) != null' "$SCHEMA_FILE" >/dev/null 2>&1; then
          allowed="$(jq -r --arg k "$key" '.properties[$k].enum | join(", ")' "$SCHEMA_FILE")"
          enum_errors+=("$key: invalid value '$val' (allowed: $allowed) (line ${ENV_LINE[$key]:-?})")
        fi
      fi
    fi

    # Range validation (minimum/maximum) for numbers/integers
    if [[ "$expected_type" == "integer" || "$expected_type" == "number" ]]; then
      if jq -e --arg k "$key" '.properties[$k].minimum? != null' "$SCHEMA_FILE" >/dev/null 2>&1; then
        minv="$(jq -r --arg k "$key" '.properties[$k].minimum' "$SCHEMA_FILE")"
        if awk "BEGIN{exit !($val < $minv)}" 2>/dev/null; then
          range_errors+=("$key: value $val is < minimum $minv (line ${ENV_LINE[$key]:-?})")
        fi
      fi
      if jq -e --arg k "$key" '.properties[$k].maximum? != null' "$SCHEMA_FILE" >/dev/null 2>&1; then
        maxv="$(jq -r --arg k "$key" '.properties[$k].maximum' "$SCHEMA_FILE")"
        if awk "BEGIN{exit !($val > $maxv)}" 2>/dev/null; then
          range_errors+=("$key: value $val is > maximum $maxv (line ${ENV_LINE[$key]:-?})")
        fi
      fi
    fi

    # Minimum-length validation for strings. Rejects unset placeholders such as
    # CHANGEME so secrets must be replaced with real values before the stack runs.
    if [[ "$expected_type" == "string" ]]; then
      if jq -e --arg k "$key" '.properties[$k].minLength? != null' "$SCHEMA_FILE" >/dev/null 2>&1; then
        minlen="$(jq -r --arg k "$key" '.properties[$k].minLength' "$SCHEMA_FILE")"
        if (( ${#val} < minlen )); then
          length_errors+=("$key: value length ${#val} is < minLength $minlen (line ${ENV_LINE[$key]:-?})")
        fi
      fi
    fi

done

# -----------------------------
# Cross-service runtime contracts
# -----------------------------

_valid_http_endpoint() {
    local value="$1" remainder authority port=""
    [[ "$value" =~ ^https?://[^[:space:]#]+$ ]] || return 1
    remainder="${value#*://}"
    authority="${remainder%%[/?]*}"
    [[ -n "$authority" && "$authority" != *"@"* ]] || return 1

    if [[ "$authority" =~ ^\[([0-9a-f:.]+)\](:([0-9]+))?$ ]]; then
        port="${BASH_REMATCH[3]}"
    elif [[ "$authority" =~ ^[a-z0-9][a-z0-9._-]*(:([0-9]+))?$ ]]; then
        port="${BASH_REMATCH[2]}"
    else
        return 1
    fi

    [[ -z "$port" ]] || {
        [[ ${#port} -le 5 ]] && (( 10#$port >= 1 && 10#$port <= 65535 ))
    }
}

embedding_model="${ENV_MAP[EMBEDDING_MODEL]-BAAI/bge-base-en-v1.5}"
embedding_model_lower="${embedding_model,,}"
embedding_artifact_name="${embedding_model_lower##*/}"
if [[ "$embedding_model_lower" == *"://"* \
   || "$embedding_artifact_name" == *"gguf"* \
   || "$embedding_artifact_name" == *"ggml"* \
   || "$embedding_artifact_name" =~ (^|[-._])q[2-8](_[a-z0-9]+)*($|[-._]) ]]; then
    contract_errors+=(
      "EMBEDDING_MODEL: bundled embeddings require a Hugging Face TEI repository ID (for example BAAI/bge-m3); URLs and GGUF/Q4 artifacts are not supported (line ${ENV_LINE[EMBEDDING_MODEL]:-?})"
    )
fi

embeddings_memory_limit="${ENV_MAP[EMBEDDINGS_MEMORY_LIMIT]-}"
if [[ -n "$embeddings_memory_limit" && ! "$embeddings_memory_limit" =~ ^[1-9][0-9]*([bBkKmMgG]|[kKmMgG][bB])?$ ]]; then
    contract_errors+=(
      "EMBEDDINGS_MEMORY_LIMIT: expected a positive Docker memory value such as 4096M, 4G, or 6GB (line ${ENV_LINE[EMBEDDINGS_MEMORY_LIMIT]:-?})"
    )
fi

rag_model="${ENV_MAP[RAG_EMBEDDING_MODEL]-}"
rag_base="${ENV_MAP[RAG_OPENAI_API_BASE_URL]-}"
rag_base="${rag_base,,}"
while [[ "$rag_base" == */ ]]; do
    rag_base="${rag_base%/}"
done
if [[ -n "$rag_base" ]] && ! _valid_http_endpoint "$rag_base"; then
    contract_errors+=(
      "RAG_OPENAI_API_BASE_URL: expected an HTTP(S) OpenAI-compatible embeddings base URL (line ${ENV_LINE[RAG_OPENAI_API_BASE_URL]:-?})"
    )
fi
case "$rag_base" in
    ""|http://embeddings:80/v1|http://embeddings/v1|http://ods-embeddings:80/v1|http://ods-embeddings/v1)
        if [[ -n "$rag_model" && "$rag_model" != "$embedding_model" ]]; then
            contract_errors+=(
              "RAG_EMBEDDING_MODEL: bundled TEI serves EMBEDDING_MODEL only; leave this override empty or configure the matching external RAG_OPENAI_API_BASE_URL (line ${ENV_LINE[RAG_EMBEDDING_MODEL]:-?})"
            )
        fi
        ;;
esac

# -----------------------------
# Reporting
# -----------------------------

had_errors=false

if (( ${#missing[@]} > 0 )); then
    had_errors=true
    log_error "Missing required keys:"
    for key in "${missing[@]}"; do
        echo "  - $key"
    done
fi

if (( ${#unknown[@]} > 0 )); then
    had_errors=true
    log_error "Unknown keys not defined in schema:"
    for key in "${unknown[@]}"; do
        echo "  - $key (line ${ENV_LINE[$key]:-?})"
    done
fi

if (( ${#type_errors[@]} > 0 )); then
    had_errors=true
    log_error "Type validation errors:"
    for err in "${type_errors[@]}"; do
        echo "  - $err"
    done
fi

if (( ${#enum_errors[@]} > 0 )); then
    had_errors=true
    log_error "Enum validation errors:"
    for err in "${enum_errors[@]}"; do
        echo "  - $err"
    done
fi

if (( ${#range_errors[@]} > 0 )); then
    had_errors=true
    log_error "Range validation errors:"
    for err in "${range_errors[@]}"; do
        echo "  - $err"
    done
fi

if (( ${#length_errors[@]} > 0 )); then
    had_errors=true
    log_error "Length validation errors (replace placeholder/default values):"
    for err in "${length_errors[@]}"; do
        echo "  - $err"
    done
fi

if (( ${#contract_errors[@]} > 0 )); then
    had_errors=true
    log_error "Runtime contract validation errors:"
    for err in "${contract_errors[@]}"; do
        echo "  - $err"
    done
fi

for key in "${!ENV_DUPLICATE_FROM[@]}"; do
  from_to="${ENV_DUPLICATE_FROM[$key]}"
  duplicate_errors+=("$key: duplicate assignment at lines $from_to")
done

if (( ${#duplicate_errors[@]} > 0 )); then
    had_errors=true
    log_error "Duplicate key errors:"
    for err in "${duplicate_errors[@]}"; do
        echo "  - $err"
    done
fi

if [[ "$had_errors" == "true" ]]; then
    echo ""
    log_info "Fix .env using .env.example as reference, then re-run:"
    echo "  ./scripts/validate-env.sh"
    exit 2
fi

log_success ".env matches schema: $SCHEMA_FILE"
log_info "Validated env file: $ENV_FILE"
log_info "Schema: $SCHEMA_FILE"
log_info "Keys in env: ${#ENV_MAP[@]}"
log_info "Keys in schema: ${#schema_keys[@]}"
log_info "Required keys: ${#required_keys[@]}"

# Optional: print helpful summary of secrets (without values)
secret_count=$(jq -r '.properties | to_entries[] | select(.value.secret==true) | .key' "$SCHEMA_FILE" | wc -l | tr -d ' ')
if [[ "$secret_count" =~ ^[0-9]+$ ]] && (( secret_count > 0 )); then
  log_info "Schema marks ${secret_count} key(s) as secrets (values not printed)."
fi

exit 0
