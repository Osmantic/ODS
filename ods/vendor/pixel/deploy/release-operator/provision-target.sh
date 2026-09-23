#!/usr/bin/env bash
set -euo pipefail
# One-time root provisioning for the narrow Pixel release operator.
#
# Usage: provision-target.sh TRANSPORT_USER PUBLIC_KEY_BASE64 STAGING_DIR
#
# TRANSPORT_USER is a new dedicated forced-command identity. PUBLIC_KEY_BASE64 is the
# base64-encoded Ed25519 public key that will be the ONLY authorized key for that
# identity. STAGING_DIR holds the reviewed files to install immutably:
#   managed.py  dispatch.py  pixel_release_grammar.py  config.json
#   templates/openclaw-gateway.service
#   templates/pixel-web-courier.service
#   templates/<unit>.prior            (optional, required by config when referenced)
#
# This grants NOPASSWD only to the fixed release-operator helper surface, never to the
# deployment owner. The transport account uses a real fixed shell (/bin/sh) because sshd
# invokes the forced command through the account shell with -c; its home, .ssh, startup
# files, and the single restricted forced-command key remain root-owned, so it has no
# generic interactive session.
[[ $EUID -eq 0 ]] || { echo "provision-target.sh must run through sudo" >&2; exit 1; }
[[ $# == 3 ]] || { echo "Usage: provision-target.sh TRANSPORT_USER PUBLIC_KEY_BASE64 STAGING_DIR" >&2; exit 2; }
transport=$1
encoded=$2
staging=$3
[[ "$transport" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || { echo "Unsafe transport user" >&2; exit 2; }
[[ -d "$staging" && ! -L "$staging" ]] || { echo "Staging directory is not a real directory" >&2; exit 2; }
public_key=$(printf '%s' "$encoded" | base64 -d 2>/dev/null || true)
[[ "$public_key" =~ ^ssh-ed25519\ [A-Za-z0-9+/=]+\ [A-Za-z0-9@._:-]+$ ]] || { echo "Transport key is not one safe Ed25519 public key" >&2; exit 2; }

for required in managed.py dispatch.py pixel_release_grammar.py config.json templates/openclaw-gateway.service templates/pixel-web-courier.service; do
  [[ -f "$staging/$required" && ! -L "$staging/$required" ]] || { echo "Staging is missing required file: $required" >&2; exit 2; }
done

# Validate the staged config/templates before install. This root-only staging validation is
# intentionally NOT part of the transport grammar or sudoers surface. It validates the
# staged config schema and cross-checks the actual staged template bytes against the
# configured SHA-256 values.
/usr/bin/python3 "$staging/managed.py" --validate-staging-config "$staging/config.json" "$staging/templates" >/dev/null

# Fail closed BEFORE any account/home/file/password mutation. $1 is the transport user.
# Rejects a transport that resolves to UID 0, a dedicated transport group that resolves to
# GID 0, or an existing transport account that belongs to any privileged group
# (root/sudo/wheel). A bad argument like `root` must never mutate the real root account.
reject_unsafe_transport() {
  local t="$1"
  local t_uid t_gid entry rc
  if entry=$(getent passwd "$t"); then
    t_uid=$(printf '%s' "$entry" | cut -d: -f3)
    if [[ ! "$t_uid" =~ ^[0-9]+$ ]]; then
      echo "Refusing: transport '$t' has a malformed numeric UID in NSS" >&2
      return 2
    fi
    if [[ "$t_uid" == 0 ]]; then
      echo "Refusing: transport '$t' resolves to UID 0 (privileged account)" >&2
      return 2
    fi
    if /usr/bin/id -nG "$t" 2>/dev/null | tr ' ' '\n' | grep -Eq '^(root|sudo|wheel)$'; then
      echo "Refusing: existing transport account '$t' belongs to a privileged group" >&2
      return 2
    fi
  else
    rc=$?
    if [[ "$rc" != 2 ]]; then
      echo "Refusing: NSS lookup for transport user '$t' failed (rc=$rc)" >&2
      return 2
    fi
  fi
  if entry=$(getent group "$t"); then
    t_gid=$(printf '%s' "$entry" | cut -d: -f3)
    if [[ ! "$t_gid" =~ ^[0-9]+$ ]]; then
      echo "Refusing: transport group '$t' has a malformed numeric GID in NSS" >&2
      return 2
    fi
    if [[ "$t_gid" == 0 ]]; then
      echo "Refusing: transport group '$t' resolves to GID 0 (privileged group)" >&2
      return 2
    fi
  else
    rc=$?
    if [[ "$rc" != 2 ]]; then
      echo "Refusing: NSS lookup for transport group '$t' failed (rc=$rc)" >&2
      return 2
    fi
  fi
  return 0
}

transport_group=$transport
transport_home="/var/lib/$transport"
reject_unsafe_transport "$transport"
getent group "$transport_group" >/dev/null || groupadd --system "$transport_group"
# Create the transport identity if absent; otherwise keep it but normalize its fixed
# attributes. Normalizing the primary gid covers a pre-existing account that may have been
# created with a different primary group, so it can never retain one. usermod is
# idempotent, so reruns remain safe.
getent passwd "$transport" >/dev/null || useradd --system --gid "$transport_group" --home-dir "$transport_home" --create-home --shell /bin/sh "$transport"
/usr/sbin/usermod --gid "$transport_group" --home "$transport_home" --shell /bin/sh "$transport"

install -d -o root -g root -m 0755 "$transport_home" "$transport_home/.ssh" /usr/local/libexec /etc/pixel-release-operator/templates
install -d -o root -g root -m 0755 /var/lib/pixel-release-operator
install -d -o root -g root -m 0700 /var/lib/pixel-release-operator/receipts
for startup in .profile .bashrc .bash_profile .bash_login; do
  install -o root -g root -m 0644 /dev/null "$transport_home/$startup"
done
install -o root -g root -m 0644 /dev/null "$transport_home/.ssh/rc"

temporary=$(mktemp)
trap 'rm -f -- "$temporary"' EXIT
printf 'restrict,command="/usr/local/libexec/pixel-release-dispatch" %s\n' "$public_key" > "$temporary"
install -o root -g root -m 0644 "$temporary" "$transport_home/.ssh/authorized_keys"

install -o root -g root -m 0755 "$staging/managed.py" /usr/local/libexec/pixel-release-managed
install -o root -g root -m 0755 "$staging/dispatch.py" /usr/local/libexec/pixel-release-dispatch
install -o root -g root -m 0644 "$staging/pixel_release_grammar.py" /usr/local/libexec/pixel_release_grammar.py
install -o root -g root -m 0644 "$staging/config.json" /etc/pixel-release-operator/config.json
install -o root -g root -m 0644 "$staging/templates/openclaw-gateway.service" /etc/pixel-release-operator/templates/openclaw-gateway.service
install -o root -g root -m 0644 "$staging/templates/pixel-web-courier.service" /etc/pixel-release-operator/templates/pixel-web-courier.service
if [[ -f "$staging/templates/openclaw-gateway.service.prior" ]]; then
  install -o root -g root -m 0644 "$staging/templates/openclaw-gateway.service.prior" /etc/pixel-release-operator/templates/openclaw-gateway.service.prior
fi
if [[ -f "$staging/templates/pixel-web-courier.service.prior" ]]; then
  install -o root -g root -m 0644 "$staging/templates/pixel-web-courier.service.prior" /etc/pixel-release-operator/templates/pixel-web-courier.service.prior
fi

# Root ownership and modes are the primary immutability boundary. We deliberately do not
# set immutable filesystem flags, which would silently block later reviewed re-provision
# updates; reruns remain recoverable and explicit.
if /usr/bin/id -nG "$transport" | tr ' ' '\n' | grep -Eq '^(root|sudo|wheel)$'; then
  echo "$transport unexpectedly belongs to a privileged group" >&2
  exit 1
fi
[[ $(stat -c '%U:%G:%a' "$transport_home") == root:root:755 ]] || { echo "Transport home is not root-owned" >&2; exit 1; }
[[ $(stat -c '%U:%G:%a' "$transport_home/.ssh") == root:root:755 ]] || { echo "Transport SSH directory is not root-owned" >&2; exit 1; }
[[ $(stat -c '%U:%G:%a' "$transport_home/.ssh/authorized_keys") == root:root:644 ]] || { echo "Transport authorized_keys is not root-owned" >&2; exit 1; }
[[ $(getent passwd "$transport" | cut -d: -f7) == /bin/sh ]] || { echo "Transport login shell is not the fixed /bin/sh" >&2; exit 1; }

# Lock the transport password so only the restricted forced-command key grants access.
/usr/bin/passwd -l "$transport" >/dev/null
# Fail closed unless the account's password status is actually locked. Debian passwd -S
# reports 'L' for a locked account; some Debian-derived variants prefix the locked shadow
# marker with '!'. Any other status means the lock did not take effect and must abort.
password_status=$(/usr/bin/passwd -S "$transport" 2>/dev/null | awk '{print $2}')
case "$password_status" in
  L|l|"!"*)
    ;;
  *)
    echo "Transport password is not verified locked (status: ${password_status:-unreadable})" >&2
    exit 1
    ;;
esac
# Clear all supplementary groups; the transport identity must carry only its primary group.
/usr/sbin/usermod -G "" "$transport"
# Verify the identity carries exactly one group: its primary group, which must be the
# dedicated transport group (no supplementary groups, no foreign primary gid).
transport_primary_gid=$(/usr/bin/id -g "$transport")
expected_transport_gid=$(getent group "$transport_group" | cut -d: -f3)
[[ "$transport_primary_gid" == "$expected_transport_gid" ]] || { echo "Transport primary gid is not the dedicated transport group" >&2; exit 1; }
read -r -a transport_groups < <(/usr/bin/id -Gn "$transport")
[[ ${#transport_groups[@]} == 1 && "${transport_groups[0]}" == "$transport_group" ]] || { echo "Transport identity has unexpected supplementary groups" >&2; exit 1; }

# Provision the single root-owned known_hosts file from the exact local Ed25519 host public
# key so the owner client can pin host identity strictly to 127.0.0.1.
host_key_file=/etc/ssh/ssh_host_ed25519_key.pub
[[ -f "$host_key_file" && ! -L "$host_key_file" ]] || { echo "Local Ed25519 host public key is missing" >&2; exit 1; }
# The host public key that is pinned into the known_hosts trust anchor must itself be
# root-owned and not group/world writable before we trust it.
[[ $(stat -c '%U:%G:%a' "$host_key_file") == root:root:644 ]] || { echo "Local Ed25519 host public key is not root-owned mode 0644" >&2; exit 1; }
read -r host_key_type host_key_b64 _ < "$host_key_file" || { echo "Cannot read the local Ed25519 host key" >&2; exit 1; }
[[ "$host_key_type" == ssh-ed25519 && "$host_key_b64" =~ ^[A-Za-z0-9+/=]+$ ]] || { echo "Local host key is not a single Ed25519 public key" >&2; exit 1; }
known_tmp=$(mktemp)
trap 'rm -f -- "$temporary" "$known_tmp"' EXIT
printf '127.0.0.1 %s %s\n' "$host_key_type" "$host_key_b64" > "$known_tmp"
install -o root -g root -m 0644 "$known_tmp" /etc/pixel-release-operator/known_hosts
[[ $(stat -c '%U:%G:%a' /etc/pixel-release-operator/known_hosts) == root:root:644 ]] || { echo "known_hosts is not root-owned mode 0644" >&2; exit 1; }
mapfile -t known_lines < /etc/pixel-release-operator/known_hosts
[[ ${#known_lines[@]} == 1 && "$known_lines" == "127.0.0.1 ssh-ed25519 $host_key_b64" ]] || { echo "known_hosts content is invalid" >&2; exit 1; }

# Post-copy integrity check: verify exact staged-to-installed SHA-256 equality for every
# expected installed file (helpers, config, templates, grammar, and the known_hosts trust
# anchor) before granting the sudoers surface. This is a post-copy check, not a claim that
# a user-owned staging tree is itself a root trust anchor.
integrity_ok=1
for pair in \
  "$staging/managed.py:/usr/local/libexec/pixel-release-managed" \
  "$staging/dispatch.py:/usr/local/libexec/pixel-release-dispatch" \
  "$staging/pixel_release_grammar.py:/usr/local/libexec/pixel_release_grammar.py" \
  "$staging/config.json:/etc/pixel-release-operator/config.json" \
  "$staging/templates/openclaw-gateway.service:/etc/pixel-release-operator/templates/openclaw-gateway.service" \
  "$staging/templates/pixel-web-courier.service:/etc/pixel-release-operator/templates/pixel-web-courier.service" \
  "$known_tmp:/etc/pixel-release-operator/known_hosts"; do
  staged=${pair%%:*}
  installed=${pair#*:}
  staged_sha=$(sha256sum "$staged" | awk '{print $1}')
  installed_sha=$(sha256sum "$installed" | awk '{print $1}')
  if [[ "$staged_sha" != "$installed_sha" ]]; then
    echo "Integrity mismatch for $installed" >&2
    integrity_ok=0
  fi
done
for prior in openclaw-gateway.service.prior pixel-web-courier.service.prior; do
  if [[ -f "$staging/templates/$prior" ]]; then
    staged_sha=$(sha256sum "$staging/templates/$prior" | awk '{print $1}')
    installed_sha=$(sha256sum "/etc/pixel-release-operator/templates/$prior" | awk '{print $1}')
    if [[ "$staged_sha" != "$installed_sha" ]]; then
      echo "Integrity mismatch for /etc/pixel-release-operator/templates/$prior" >&2
      integrity_ok=0
    fi
  fi
done
[[ $integrity_ok == 1 ]] || { echo "Staged-to-installed integrity check failed" >&2; exit 1; }

# Validate the installed root config and helper BEFORE installing any sudoers surface, so a
# post-grant validation failure can never leave a live partial grant.
/usr/bin/python3 /usr/local/libexec/pixel-release-managed --validate-config >/dev/null

sudoers_temporary=$(mktemp)
trap 'rm -f -- "$temporary" "$known_tmp" "$sudoers_temporary"' EXIT
cat > "$sudoers_temporary" <<EOF
Cmnd_Alias PIXEL_RELEASE_MANAGED = \\
  /usr/local/libexec/pixel-release-managed status, \\
  /usr/local/libexec/pixel-release-managed --validate-config, \\
  /usr/local/libexec/pixel-release-managed unit install gateway *, \\
  /usr/local/libexec/pixel-release-managed unit install courier *, \\
  /usr/local/libexec/pixel-release-managed unit remove gateway, \\
  /usr/local/libexec/pixel-release-managed unit remove courier, \\
  /usr/local/libexec/pixel-release-managed systemctl daemon-reload, \\
  /usr/local/libexec/pixel-release-managed systemctl enable gateway, \\
  /usr/local/libexec/pixel-release-managed systemctl enable gateway --now, \\
  /usr/local/libexec/pixel-release-managed systemctl enable courier, \\
  /usr/local/libexec/pixel-release-managed systemctl enable courier --now, \\
  /usr/local/libexec/pixel-release-managed systemctl disable gateway, \\
  /usr/local/libexec/pixel-release-managed systemctl disable gateway --now, \\
  /usr/local/libexec/pixel-release-managed systemctl disable courier, \\
  /usr/local/libexec/pixel-release-managed systemctl disable courier --now, \\
  /usr/local/libexec/pixel-release-managed systemctl restart gateway, \\
  /usr/local/libexec/pixel-release-managed systemctl restart courier, \\
  /usr/local/libexec/pixel-release-managed systemctl stop gateway, \\
  /usr/local/libexec/pixel-release-managed systemctl stop courier, \\
  /usr/local/libexec/pixel-release-managed systemctl is-active gateway, \\
  /usr/local/libexec/pixel-release-managed systemctl is-active courier, \\
  /usr/local/libexec/pixel-release-managed systemctl is-enabled gateway, \\
  /usr/local/libexec/pixel-release-managed systemctl is-enabled courier, \\
  /usr/local/libexec/pixel-release-managed probe ops, \\
  /usr/local/libexec/pixel-release-managed probe frontier
$transport ALL=(root) NOPASSWD: PIXEL_RELEASE_MANAGED
EOF
/usr/sbin/visudo -cf "$sudoers_temporary" >/dev/null
install -o root -g root -m 0440 "$sudoers_temporary" /etc/sudoers.d/pixel-release-operator

printf '%s\n' "transport=$transport" "ssh=public-key-only/restrict/forced-command" "authority=release-operator-only" "owner-sudo=password-backed-only"
