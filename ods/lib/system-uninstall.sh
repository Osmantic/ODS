#!/usr/bin/env bash
# Imported by the uninstaller; no work happens until this function is called.
ods_uninstall_system_units() {
    local install_dir="$1" owner_home="$2"
    local systemd_dir="${ODS_UNINSTALL_SYSTEMD_DIR:-/etc/systemd/system}"
    local root_uid="${ODS_UNINSTALL_SYSTEMD_UID:-0}"
    local unit file fragment dropins state
    local -a owned=()
    [[ "$systemd_dir" == /* && "$systemd_dir" != / ]] || return 1

    # Validate every candidate before stopping either service. Matching the
    # rendered installer template binds both execution and environment paths.
    for unit in ods-host-agent.service ods-mdns.service; do
        file="$systemd_dir/$unit"
        [[ -e "$file" || -L "$file" ]] || continue
        python3 - "$file" "$install_dir" "$owner_home" "$root_uid" "$unit" <<'PY' || return 1
import os, pathlib, re, stat, sys
file, install, home, root_uid, unit = sys.argv[1:]
file = pathlib.Path(file)
try:
    info = file.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != int(root_uid)
            or info.st_mode & 0o022 or info.st_nlink != 1
            or any(p.is_symlink() for p in file.parents)):
        raise ValueError('unsafe systemd unit custody')
    template = (pathlib.Path(install)/'scripts/systemd'/unit).read_text()
    pattern = re.escape(template)
    for key, value in {'__INSTALL_DIR__': re.escape(install), '__HOME__': re.escape(home),
                       '__PYTHON3__': r'/[^\s\n]+', '__INSTALL_USER__': r'[a-zA-Z0-9_.-]+'}.items():
        pattern = pattern.replace(re.escape(key), value)
    if not re.fullmatch(pattern, file.read_text()):
        raise ValueError('unit is foreign or differs from the installed ODS template')
except (OSError, ValueError) as error:
    print(f'Refusing to remove {unit}: {error}', file=sys.stderr)
    raise SystemExit(1)
PY
        fragment=$(systemctl show "$unit" --property=FragmentPath --value) || return 1
        dropins=$(systemctl show "$unit" --property=DropInPaths --value) || return 1
        if [[ ( -n "$fragment" && "$fragment" != "$file" ) || -n "$dropins" ]]; then
            log_error "Systemd overrides $unit; its files and installation were retained"
            return 1
        fi
        owned+=("$unit")
    done
    [[ ${#owned[@]} -gt 0 ]] || return 0
    prepare_sudo_credential || return 1
    for unit in "${owned[@]}"; do
        # Disabled services can still be active. Stop every verified unit and
        # retain its definition on any failure so cleanup can be retried.
        if ! run_sudo timeout 30s systemctl disable --now "$unit"; then
            log_error "Could not stop $unit; its files and installation were retained"
            return 1
        fi
        state=$(systemctl show "$unit" --property=ActiveState --value) || return 1
        case "$state" in
            inactive|failed) ;;
            *) log_error "$unit is not stopped; its files and installation were retained"; return 1 ;;
        esac
    done
    for unit in "${owned[@]}"; do
        run_sudo rm -f -- "$systemd_dir/$unit" || return 1
    done
    run_sudo systemctl daemon-reload || return 1
}
