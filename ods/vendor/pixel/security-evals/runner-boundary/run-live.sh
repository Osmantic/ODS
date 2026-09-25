#!/usr/bin/env bash
set -euo pipefail

[[ $# == 3 ]] || { echo "Usage: run-live.sh TARGET_ID OPERATOR_SSH_ALIAS EXPECTED_HOSTNAME" >&2; exit 2; }
target=$1
operator_alias=$2
expected=$3
[[ "$target" =~ ^[a-z][a-z0-9_-]{1,63}$ ]] || { echo "Unsafe target ID" >&2; exit 2; }
[[ "$operator_alias" =~ ^[A-Za-z0-9_.@:-]{1,255}$ ]] || { echo "Unsafe operator alias" >&2; exit 2; }
[[ "$expected" =~ ^[A-Za-z0-9._-]{1,255}$ ]] || { echo "Unsafe expected hostname" >&2; exit 2; }

broker_user=${PIXEL_OPS_BROKER_USER:-pixel-ops-broker}
observed=$(sudo -u "$broker_user" ssh "$target" hostname 2>/dev/null | tr -d '\r')
[[ "$observed" == "$expected" ]] || { echo "Broker runner identity probe failed" >&2; exit 1; }
if sudo -u "$broker_user" ssh "$target" id >/dev/null 2>&1; then
  echo "Forced command accepted an untyped identity command" >&2
  exit 1
fi
if sudo -u "$broker_user" ssh "$target" 'cd -- /var/lib/pixel-runner/jobs && exec /bin/sh -c id' >/dev/null 2>&1; then
  echo "Forced command accepted a shell" >&2
  exit 1
fi

ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "$operator_alias" "sudo bash -s -- '$expected'" <<'REMOTE'
set -euo pipefail
expected=$1
transport=pixel-ops-transport
workload=pixel-runner
transport_home=/var/lib/pixel-ops-transport
workload_home=/var/lib/pixel-runner
[[ $(hostname) == "$expected" ]]
transport_uid=$(id -u "$transport")
workload_uid=$(id -u "$workload")
[[ "$transport_uid" != "$workload_uid" ]]
[[ $(getent passwd "$workload" | cut -d: -f7) == /usr/sbin/nologin ]]
[[ $(stat -c '%U:%G:%a' "$transport_home") == root:root:755 ]]
[[ $(stat -c '%U:%G:%a' "$transport_home/.ssh") == root:root:755 ]]
[[ $(stat -c '%U:%G:%a' "$transport_home/.ssh/authorized_keys") == root:root:644 ]]
[[ $(wc -l < "$transport_home/.ssh/authorized_keys") == 1 ]]
grep -Eq '^restrict,command="/usr/local/libexec/pixel-ops-dispatch --job-root /var/lib/pixel-runner/jobs --workload-user pixel-runner" ssh-ed25519 ' "$transport_home/.ssh/authorized_keys"
[[ ! -s "$workload_home/.ssh/authorized_keys" ]]
if sudo -u "$workload" sudo --non-interactive --user root -- /usr/local/libexec/pixel-ops-managed --validate-config >/dev/null 2>&1; then
  echo "Workload identity can invoke managed root actions" >&2
  exit 1
fi
workload_observed=$(sudo -u "$transport" sudo --non-interactive --user "$workload" -- /usr/local/libexec/pixel-ops-dispatch --workload --job-root "$workload_home/jobs" --workload-user "$workload" --cwd "$workload_home/jobs" -- /bin/hostname | tr -d '\r')
[[ "$workload_observed" == "$expected" ]]
sudo -u "$transport" sudo --non-interactive --user root -- /usr/local/libexec/pixel-ops-managed --validate-config >/dev/null
REMOTE

printf '%s\n' '{"status":"pass","forcedCommand":"pass","arbitraryShell":"denied","transportWorkloadSeparation":"pass","workloadSudo":"denied","typedManagedRoute":"pass"}'
