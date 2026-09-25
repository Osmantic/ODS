#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT

cat >"$tmp/powershell.exe" <<'PS'
#!/usr/bin/env bash
if [[ "${ODS_TEST_PS_MODE:-ok}" == fail ]]; then
    printf 'Exec format error\n' >&2
    exit 126
fi
printf '17179869184\r\n'
PS
cat >"$tmp/wmic.exe" <<'WMIC'
#!/usr/bin/env bash
if [[ "${ODS_TEST_WMIC_MODE:-ok}" == fail ]]; then
    printf 'Exec format error\n' >&2
    exit 126
fi
printf 'TotalVisibleMemorySize=33554432\r\n'
WMIC
chmod +x "$tmp/powershell.exe" "$tmp/wmic.exe"

export PATH="$tmp:$PATH"
source "$root/installers/lib/wsl-memory.sh"

[[ "$(ods_wsl_host_ram_kb)" == 16777216 ]]
ODS_TEST_PS_MODE=fail
export ODS_TEST_PS_MODE
[[ "$(ods_wsl_host_ram_kb)" == 33554432 ]]
ODS_TEST_WMIC_MODE=fail
export ODS_TEST_WMIC_MODE
if ods_wsl_host_ram_kb >"$tmp/failed.out"; then
    printf 'FAIL: unavailable Windows interop supplied host RAM\n' >&2
    exit 1
fi
[[ ! -s "$tmp/failed.out" ]]

# This is the installer call shape under set -euo pipefail: a failed optional
# Windows query must leave the caller alive to use Linux /proc/meminfo.
vm_kb=49209324
host_kb="$(ods_wsl_host_ram_kb)" || host_kb=""
ram_kb="${host_kb:-$vm_kb}"
[[ "$ram_kb" == "$vm_kb" ]]

phase="$root/installers/phases/02-detection.sh"
[[ "$(grep -Fc 'ods_wsl_host_ram_kb)" || _wsl_' "$phase")" == 2 ]]

printf 'PASS: WSL host-RAM lookup falls back when Windows interop exits 126\n'
