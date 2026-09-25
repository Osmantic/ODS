#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "provision-target.sh must run through sudo" >&2; exit 1; }
[[ $# == 3 ]] || { echo "Usage: provision-target.sh TRANSPORT_USER WORKLOAD_USER PUBLIC_KEY_BASE64" >&2; exit 2; }
transport=$1
runner=$2
encoded=$3
[[ "$transport" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || { echo "Unsafe transport user" >&2; exit 2; }
[[ "$runner" =~ ^[a-z_][a-z0-9_-]{0,31}$ && "$runner" != "$transport" ]] || { echo "Unsafe or non-separated workload user" >&2; exit 2; }
public_key=$(printf '%s' "$encoded" | base64 -d)
[[ "$public_key" =~ ^ssh-ed25519\ [A-Za-z0-9+/=]+\ [A-Za-z0-9@._:-]+$ ]] || { echo "Runner key is not one safe Ed25519 public key" >&2; exit 2; }
runner_group=$runner
runner_home="/var/lib/$runner"
transport_group=$transport
transport_home="/var/lib/$transport"
getent group "$runner_group" >/dev/null || groupadd --system "$runner_group"
getent passwd "$runner" >/dev/null || useradd --system --gid "$runner_group" --home-dir "$runner_home" --create-home --shell /usr/sbin/nologin "$runner"
usermod --home "$runner_home" --shell /usr/sbin/nologin "$runner"
getent group "$transport_group" >/dev/null || groupadd --system "$transport_group"
getent passwd "$transport" >/dev/null || useradd --system --gid "$transport_group" --home-dir "$transport_home" --create-home --shell /bin/sh "$transport"
usermod --home "$transport_home" --shell /bin/sh "$transport"
# Hostile test/build code runs only as the workload user. The broker key logs in as a
# distinct transport user whose home, key, startup files, and forced command are all
# root-owned. Only the transport identity receives the narrow sudo policy.
install -d -o root -g root -m 0755 "$runner_home" "$runner_home/.ssh" "$transport_home" "$transport_home/.ssh"
install -d -o "$runner" -g "$runner_group" -m 0700 "$runner_home/jobs"
for startup in .profile .bashrc .bash_profile .bash_login; do
  install -o root -g root -m 0644 /dev/null "$runner_home/$startup"
  install -o root -g root -m 0644 /dev/null "$transport_home/$startup"
done
install -o root -g root -m 0644 /dev/null "$transport_home/.ssh/rc"
install -o root -g root -m 0644 /dev/null "$runner_home/.ssh/rc"
install -o root -g root -m 0644 /dev/null "$runner_home/.ssh/authorized_keys"
temporary=$(mktemp)
trap 'rm -f -- "$temporary"' EXIT
printf 'restrict,command="/usr/local/libexec/pixel-ops-dispatch --job-root %s/jobs --workload-user %s" %s\n' "$runner_home" "$runner" "$public_key" >> "$temporary"
install -o root -g root -m 0644 "$temporary" "$transport_home/.ssh/authorized_keys"
install -d -o root -g root -m 0755 /usr/local/libexec
sudoers_temporary=$(mktemp)
managed_sudoers_temporary=$(mktemp)
trap 'rm -f -- "$temporary" "$sudoers_temporary" "$managed_sudoers_temporary"' EXIT
cat > "$sudoers_temporary" <<EOF
Cmnd_Alias PIXEL_OPS_WORKLOAD_DISPATCH = /usr/local/libexec/pixel-ops-dispatch --workload *
$transport ALL=($runner) NOPASSWD: PIXEL_OPS_WORKLOAD_DISPATCH
EOF
visudo -cf "$sudoers_temporary" >/dev/null
install -o root -g root -m 0440 "$sudoers_temporary" /etc/sudoers.d/pixel-ops-workload
cat > "$managed_sudoers_temporary" <<EOF
Cmnd_Alias PIXEL_OPS_MANAGED = /usr/local/libexec/pixel-ops-managed service *, /usr/local/libexec/pixel-ops-managed deploy *, /usr/local/libexec/pixel-ops-managed package *, /usr/local/libexec/pixel-ops-managed host reboot approved, /usr/local/libexec/pixel-ops-managed --validate-config
$transport ALL=(root) NOPASSWD: PIXEL_OPS_MANAGED
EOF
visudo -cf "$managed_sudoers_temporary" >/dev/null
install -o root -g root -m 0440 "$managed_sudoers_temporary" /etc/sudoers.d/pixel-ops-managed
for account in "$runner" "$transport"; do
  if id -nG "$account" | tr ' ' '\n' | grep -Eq '^(sudo|wheel)$'; then
    echo "$account unexpectedly belongs to a privileged group" >&2
    exit 1
  fi
done
[[ $(stat -c '%U:%G:%a' "$runner_home") == root:root:755 ]] || { echo "Workload home is not root-owned" >&2; exit 1; }
[[ ! -s "$runner_home/.ssh/authorized_keys" ]] || { echo "Workload identity unexpectedly has an SSH key" >&2; exit 1; }
[[ $(stat -c '%U:%G:%a' "$transport_home") == root:root:755 ]] || { echo "Transport home is not root-owned" >&2; exit 1; }
[[ $(stat -c '%U:%G:%a' "$transport_home/.ssh") == root:root:755 ]] || { echo "Transport SSH directory is not root-owned" >&2; exit 1; }
[[ $(stat -c '%U:%G:%a' "$transport_home/.ssh/authorized_keys") == root:root:644 ]] || { echo "Transport authorized_keys is not root-owned" >&2; exit 1; }
[[ $(getent passwd "$runner" | cut -d: -f7) == /usr/sbin/nologin ]] || { echo "Workload login shell is not disabled" >&2; exit 1; }
printf '%s\n' "transport=$transport" "workload=$runner" "ssh=public-key-only/restrict/forced-command" "authority=transport-only" "workload-privilege-escalation=denied"
