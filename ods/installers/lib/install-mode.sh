#!/usr/bin/env bash
# Preserve an existing explicit ODS runtime mode across installer reruns.

ods_existing_install_mode() {
    local env_file="$1" expected_uid="${2:-${UID:-$(id -u)}}"
    local owner_uid file_mode file_size line value="" found=false
    local modes='local|cloud|hybrid|lemonade' value_pattern
    # Accept literal dotenv formatting without evaluating any shell content.
    value_pattern="^[[:space:]]*(($modes)|\"($modes)\"|'($modes)')([[:space:]]+#.*)?[[:space:]]*$"

    [[ -f "$env_file" && ! -L "$env_file" ]] || return 1
    read -r owner_uid file_mode file_size < <(
        stat -c '%u %a %s' -- "$env_file" 2>/dev/null
    ) || return 1
    [[ "$owner_uid" == "$expected_uid" ]] || return 1
    [[ "$file_mode" =~ ^[0-7]{3,4}$ && "$file_size" =~ ^[0-9]+$ ]] || return 1
    (( (8#$file_mode & 8#022) == 0 )) || return 1
    (( file_size > 0 && file_size <= 1048576 )) || return 1

    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%$'\r'}"
        [[ "$line" =~ ^[[:space:]]*ODS_MODE[[:space:]]*= ]] || continue
        [[ "$found" == "false" ]] || return 1
        found=true
        [[ "${line#*=}" =~ $value_pattern ]] || return 1
        value="${BASH_REMATCH[2]}${BASH_REMATCH[3]}${BASH_REMATCH[4]}"
    done <"$env_file"

    [[ "$found" == "true" ]] || return 1
    # Compatibility read for one release: "lemonade" was the managed AMD local
    # mode. The Lemonade migration rewrites it; an unmigrated value is local.
    [[ "$value" != lemonade ]] || value=local
    printf '%s\n' "$value"
}

# An API connected in Settings > Remote model marks the install ODS_MODE=cloud
# while it is active, because its route goes through the cloud gateway. That
# is not the install's mode: the route's activation record keeps the mode it
# replaced. Prints that mode while an active or staging route explains the
# marker. (An upgrade that took the marker for a --cloud install replaced the
# local model with hosted defaults and took the Portal down.)
ods_remote_route_previous_mode() {
    local record="$1/data/remote-provider/activation-state.json"
    [[ -f "$record" && ! -L "$record" ]] || return 1
    command -v python3 >/dev/null 2>&1 || return 1
    python3 - "$record" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        record = json.load(handle)
except (OSError, ValueError):
    sys.exit(1)
previous = record.get("previous") if isinstance(record, dict) else None
mode = previous.get("odsMode") if isinstance(previous, dict) else None
if record.get("phase") not in ("active", "staging") or mode not in ("local", "hybrid"):
    sys.exit(1)
print(mode)
PY
}

# Succeeds only for an active or staging model API (Settings > Remote model)
# record. It prints the LLM_API_URL that API replaced when the record's
# previous mode is the mode this install keeps ($2); otherwise nothing, and
# the caller uses that mode's default.
ods_remote_route_previous_api_url() {
    local record="$1/data/remote-provider/activation-state.json"
    [[ -f "$record" && ! -L "$record" ]] || return 1
    command -v python3 >/dev/null 2>&1 || return 1
    python3 - "$record" "$2" <<'PY'
import json
import re
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        record = json.load(handle)
except (OSError, ValueError):
    sys.exit(1)
if not isinstance(record, dict) or record.get("phase") not in ("active", "staging"):
    sys.exit(1)
previous = record.get("previous") if isinstance(record.get("previous"), dict) else {}
url = previous.get("llmApiUrl")
if (previous.get("odsMode") == sys.argv[2] and isinstance(url, str)
        and re.fullmatch(r"https?://[!-~]{1,2040}", url)):
    print(url)
PY
}

ods_preserve_existing_install_mode() {
    local current_mode="$1" mode_explicit="$2" env_file="$3" existing_mode remote_mode

    if [[ "$mode_explicit" == "true" ]]; then
        printf '%s\n' "$current_mode"
        return 0
    fi
    if existing_mode="$(ods_existing_install_mode "$env_file")"; then
        if [[ "$existing_mode" == cloud ]] \
            && remote_mode="$(ods_remote_route_previous_mode "$(dirname "$env_file")")"; then
            existing_mode="$remote_mode"
        fi
        printf '%s\n' "$existing_mode"
    else
        printf '%s\n' "$current_mode"
    fi
}
