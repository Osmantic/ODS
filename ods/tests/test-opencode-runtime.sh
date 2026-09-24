#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
. "$ROOT/installers/lib/opencode-runtime.sh"
scratch="$(mktemp -d)"
trap 'rm -rf -- "$scratch"' EXIT
export HOME="$scratch/home"
mkdir -p "$HOME/.opencode/bin" "$HOME/.config/opencode" "$scratch/package"
printf 'custom config\n' > "$HOME/.config/opencode/opencode.json"
printf '#!/bin/sh\necho 1.18.32\n' > "$scratch/package/opencode"
chmod +x "$scratch/package/opencode"
tar -czf "$scratch/good.tar.gz" -C "$scratch/package" opencode
digest="$(sha256sum "$scratch/good.tar.gz")"; digest="${digest%% *}"
ods_opencode_release() { printf '1.18.32\tLinux\tx86_64\topencode-linux-x64-baseline.tar.gz\t%s\n' "$digest"; }
curl() { [[ "$*" == *'/v1.18.32/opencode-linux-x64-baseline.tar.gz'* ]] || return 1; printf '.\n' >> "$scratch/downloads"; cp "$scratch/good.tar.gz" "${@: -1}"; }
old() { printf '#!/bin/sh\necho 1.2.18\n' > "$HOME/.opencode/bin/opencode"; chmod +x "$HOME/.opencode/bin/opencode"; }
old
result="$(ods_install_opencode "$HOME/.opencode/bin/opencode")"
[[ "$result" == "$HOME/.opencode/bin/opencode" && "$("$result" --version)" == 1.18.32 ]]
echo 'PASS upgrade older owned binary'
ods_install_opencode "$result" >/dev/null
[[ "$(wc -l < "$scratch/downloads")" == 1 ]]
echo 'PASS current binary causes no download'
old; digest=wrong
if ods_install_opencode "$result" >/dev/null 2>&1; then exit 1; fi
[[ "$("$result" --version)" == 1.2.18 ]]
echo 'PASS corrupt archive preserves old executable'
digest="$(sha256sum "$scratch/good.tar.gz")"; digest="${digest%% *}"
printf '#!/bin/sh\necho 0.0.0\n' > "$scratch/package/opencode"
tar -czf "$scratch/good.tar.gz" -C "$scratch/package" opencode
digest="$(sha256sum "$scratch/good.tar.gz")"; digest="${digest%% *}"
if ods_install_opencode "$result" >/dev/null 2>&1; then exit 1; fi
[[ "$("$result" --version)" == 1.2.18 ]]
echo 'PASS wrong staged version preserves old executable'
printf '#!/bin/sh\necho 1.18.32\nexit 1\n' > "$scratch/package/opencode"
tar -czf "$scratch/good.tar.gz" -C "$scratch/package" opencode
digest="$(sha256sum "$scratch/good.tar.gz")"; digest="${digest%% *}"
if ods_install_opencode "$result" >/dev/null 2>&1; then exit 1; fi
[[ "$("$result" --version)" == 1.2.18 ]]
echo 'PASS matching version text with failed process exit rejected'
[[ "$(cat "$HOME/.config/opencode/opencode.json")" == 'custom config' ]]
[[ -z "$(find "$HOME/.opencode/bin" -name '.ods-update.*')" ]]
echo 'PASS config preserved and owned staging cleaned'
# Platform selection uses the shared reviewed manifest, including conservative
# x64 baseline builds for machines without AVX2 and native Apple/Windows ARM.
. "$ROOT/installers/lib/opencode-runtime.sh"
for pair in 'Linux x86_64' 'Linux aarch64' 'Darwin arm64' 'Darwin x86_64'; do
    read -r test_platform test_arch <<< "$pair"
    uname() { if [[ "$1" == -s ]]; then echo "$test_platform"; else echo "$test_arch"; fi; }
    ldd() { echo glibc; }
    [[ "$(ods_opencode_release)" == 1.18.32$'\t'* ]]
done
test_platform=Linux; test_arch=x86_64; ldd() { echo musl; }
[[ "$(ods_opencode_release)" == *baseline-musl.tar.gz* ]]
test_arch=unknown
if ods_opencode_release; then exit 1; fi
echo 'PASS supported architecture/libc selection and unsupported rejection'
# Model a service that is already active: enable alone leaves its old executable
# and environment in use. The installer must reload before an explicit restart.
unit_generation=new; loaded_generation=old; running_generation=old
service_calls=(); restart_fails=false
ods_systemctl_user() {
    service_calls+=("$*")
    case "$1" in
        daemon-reload) loaded_generation="$unit_generation" ;;
        enable) : ;;
        restart) $restart_fails && return 1; running_generation="$loaded_generation" ;;
        is-active) [[ "$running_generation" == new ]] ;;
        *) return 1 ;;
    esac
}
ods_restart_opencode_service
[[ "$running_generation" == new && "${service_calls[*]}" == 'daemon-reload enable opencode-web.service restart opencode-web.service is-active --quiet opencode-web.service' ]]
echo 'PASS existing active service reloads and restarts into new binary/environment'
restart_fails=true; service_calls=()
if ods_restart_opencode_service; then exit 1; fi
[[ "${service_calls[*]}" != *is-active* ]]
echo 'PASS failed managed restart never reports successful activation'
