#!/bin/bash
# Keep Podman's effective short-name search configuration valid and idempotent.

ods_podman_ensure_dockerhub_search() {
    local conf_dir="${XDG_CONFIG_HOME:-$HOME/.config}/containers"
    local user_conf="$conf_dir/registries.conf"
    local user_dropin="$conf_dir/registries.conf.d/99-ods-dockerhub.conf"
    local target_conf="${CONTAINERS_REGISTRIES_CONF:-$user_dropin}"
    local effective_search_json=""
    local -a source_confs=()
    local conf rc

    if [[ -n "${CONTAINERS_REGISTRIES_CONF:-}" ]]; then
        source_confs+=("$target_conf")
    else
        effective_search_json="$(podman info --format '{{json .Registries.Search}}' 2>/dev/null || true)"
        for conf in /usr/share/containers/registries.conf \
                    /usr/share/containers/registries.conf.d/*.conf \
                    /etc/containers/registries.conf \
                    /etc/containers/registries.conf.d/*.conf \
                    "$user_conf" \
                    "$conf_dir"/registries.conf.d/*.conf; do
            [[ -f "$conf" ]] && source_confs+=("$conf")
        done
    fi

    # "${source_confs[@]}" aborts under `set -u` with "unbound variable" when
    # the array is empty on Bash < 4.4 (macOS 3.2, RHEL 7). The guarded form
    # passes no extra argument instead, which the Python side already treats
    # as "no fallback sources".
    if python3 - "$target_conf" "$effective_search_json" \
        ${source_confs[@]+"${source_confs[@]}"} <<'PY'
import ast
import json
import os
import re
import sys
import tempfile
from pathlib import Path

target = Path(sys.argv[1])
effective_search_json = sys.argv[2]
sources = [Path(value) for value in sys.argv[3:]]
registries = []
effective_search_loaded = False


def strip_toml_comments(value):
    """Remove comments without treating a # inside a quoted string as one."""
    output = []
    quote = None
    escaped = False
    in_comment = False
    for character in value:
        if in_comment:
            if character == "\n":
                in_comment = False
                output.append(character)
            continue
        if quote:
            output.append(character)
            if escaped:
                escaped = False
            elif character == "\\" and quote == '"':
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in ('"', "'"):
            quote = character
            output.append(character)
        elif character == "#":
            in_comment = True
        else:
            output.append(character)
    return "".join(output)


def search_registries(path):
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except (OSError, UnicodeError) as exc:
        print(f"cannot read Podman registry config {path}: {exc}", file=sys.stderr)
        raise SystemExit(2)

    first_table = next(
        (index for index, line in enumerate(lines) if re.match(r"^[ \t]*\[", line)),
        len(lines),
    )
    key_line = next(
        (
            index
            for index, line in enumerate(lines[:first_table])
            if re.match(r"^[ \t]*unqualified-search-registries[ \t]*=", line)
        ),
        None,
    )
    if key_line is None:
        return None

    expression = lines[key_line].split("=", 1)[1]
    end_line = key_line + 1
    balance = expression.count("[") - expression.count("]")
    while balance > 0 and end_line < first_table:
        expression += lines[end_line]
        balance += lines[end_line].count("[") - lines[end_line].count("]")
        end_line += 1
    if balance != 0:
        print(f"unterminated unqualified-search-registries array in {path}", file=sys.stderr)
        raise SystemExit(2)
    try:
        configured = ast.literal_eval(strip_toml_comments(expression).strip())
    except (SyntaxError, ValueError) as exc:
        print(f"invalid unqualified-search-registries value in {path}: {exc}", file=sys.stderr)
        raise SystemExit(2)
    if not isinstance(configured, list) or not all(isinstance(item, str) for item in configured):
        print(f"invalid unqualified-search-registries value in {path}", file=sys.stderr)
        raise SystemExit(2)
    return configured

if effective_search_json:
    try:
        configured = json.loads(effective_search_json)
    except json.JSONDecodeError:
        configured = None
    if isinstance(configured, list) and all(isinstance(item, str) for item in configured):
        registries.extend(configured)
        effective_search_loaded = True

if not effective_search_loaded:
    # Older Podman versions may not expose Registries.Search in `podman info`.
    # Preserve every configured fallback instead of discarding an operator's
    # registry when installing the ODS user override.
    for source in sources:
        if not source.is_file():
            continue
        configured = search_registries(source) or []
        for registry in configured:
            if registry not in registries:
                registries.append(registry)

if "docker.io" in registries:
    raise SystemExit(10)
registries.append("docker.io")

try:
    if target.is_file():
        text = target.read_text(encoding="utf-8")
    else:
        # Use a dedicated user drop-in. Copying a system registries.conf into
        # the higher-precedence user main file would hide later system updates
        # and risks dropping registry tables supplied by system drop-ins.
        text = ""
except (OSError, UnicodeError) as exc:
    print(f"cannot read Podman registry config {target}: {exc}", file=sys.stderr)
    raise SystemExit(2)

assignment = "unqualified-search-registries = " + json.dumps(registries) + "\n"
lines = text.splitlines(keepends=True)
first_table = next(
    (index for index, line in enumerate(lines) if re.match(r"^[ \t]*\[", line)),
    len(lines),
)
key_line = next(
    (
        index
        for index, line in enumerate(lines[:first_table])
        if re.match(r"^[ \t]*unqualified-search-registries[ \t]*=", line)
    ),
    None,
)

if key_line is None:
    insertion = [assignment]
    if first_table and lines[first_table - 1].strip():
        insertion.insert(0, "\n")
    lines[first_table:first_table] = insertion
else:
    end_line = key_line + 1
    balance = lines[key_line].count("[") - lines[key_line].count("]")
    while balance > 0 and end_line < first_table:
        balance += lines[end_line].count("[") - lines[end_line].count("]")
        end_line += 1
    if balance != 0:
        print(f"unterminated unqualified-search-registries array in {target}", file=sys.stderr)
        raise SystemExit(2)
    lines[key_line:end_line] = [assignment]

write_target = target.resolve() if target.is_symlink() else target
write_target.parent.mkdir(parents=True, exist_ok=True)
mode = write_target.stat().st_mode & 0o777 if write_target.exists() else 0o644
temp_name = None
try:
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(write_target.parent),
        prefix=write_target.name + ".",
        delete=False,
    ) as handle:
        temp_name = handle.name
        handle.write("".join(lines))
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temp_name, mode)
    os.replace(temp_name, str(write_target))
except OSError as exc:
    if temp_name:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
    print(f"cannot write Podman registry config {write_target}: {exc}", file=sys.stderr)
    raise SystemExit(2)
PY
    then
        rc=0
    else
        rc=$?
    fi

    case "$rc" in
        0)
            ai_ok "Configured Podman to resolve unqualified image names via Docker Hub ($target_conf)"
            ;;
        10)
            log "Podman already searches docker.io for unqualified image names"
            ;;
        *)
            ai_warn "Could not update Podman registry configuration at $target_conf"
            return "$rc"
            ;;
    esac
}
