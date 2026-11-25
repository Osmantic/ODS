#!/usr/bin/env bash
# Prepare only the WSL NAT/Desktop route; explicit operator routes stay intact.
ods_prepare_wsl_agent_address() {
    local root="$1" restart="${2:-false}" result changed reason
    [[ "$(uname -s)" == Linux ]] || return 0
    [[ -f "$root/lib/wsl-agent-address.py" ]] || return 0
    result=$(python3 "$root/lib/wsl-agent-address.py" "$root") || {
        # The helper reports a machine-readable reason. Name it, so a failed
        # networking probe is not sent to the same .env-ownership hint as an
        # unrelated failure. Any non-JSON or unexpected payload stays "unknown".
        reason=$(python3 -c 'import json,sys; v=json.load(sys.stdin).get("error"); print(v if isinstance(v,str) else "")' <<< "$result" 2>/dev/null) || reason=""
        [[ "$reason" =~ ^[a-z][a-z-]{0,31}$ ]] || reason="unknown"
        printf '%s\n' "Could not prepare the WSL host-agent address (reason: ${reason}). Check WSL networking and private .env ownership." >&2
        return 1
    }
    changed=$(python3 -c 'import json,sys; print(str(json.load(sys.stdin)["changed"]).lower())' <<< "$result") || return 1
    [[ "$restart" == true && "$changed" == true ]] || return 0
    # The fixed task name alone does not establish ownership. Check the
    # protected unit, installing user and complete executable arguments.
    if ! systemctl cat ods-host-agent.service >/dev/null 2>&1; then
        return 0
    fi
    python3 - "$root" <<'PY' || return 1
import os, pathlib, pwd, re, stat, subprocess, sys
def prop(name):
    return subprocess.check_output(['systemctl', 'show', 'ods-host-agent.service', '-p', name, '--value'], text=True).strip()
try:
    unit = pathlib.Path(prop('FragmentPath'))
    info = unit.lstat()
    assert unit == pathlib.Path('/etc/systemd/system/ods-host-agent.service')
    assert stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022
    assert prop('User') == pwd.getpwuid(os.getuid()).pw_name
    script = str(pathlib.Path(sys.argv[1]) / 'bin/ods-host-agent.py')
    pattern = r'\{ path=(/usr/(?:local/)?bin/python3(?:\.\d+)?) ; argv\[\]=\1 ' + re.escape(script) + r'(?: --require-ods-network)? ; ignore_errors=no ;.* \}'
    assert re.fullmatch(pattern, prop('ExecStart'))
except (AssertionError, OSError, subprocess.SubprocessError):
    print('Refusing to restart a host-agent unit whose installation ownership is unverified.', file=sys.stderr)
    raise SystemExit(1)
PY
    if [[ "$EUID" == 0 ]]; then
        systemctl restart ods-host-agent.service
    else
        sudo -n systemctl restart ods-host-agent.service
    fi
}
