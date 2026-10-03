#!/usr/bin/env bash
set -euo pipefail
# One-time root provisioning for the thin Pixel lifecycle and broker-byte operator surface.
#
# Usage: provision-pixel-operator.sh TRANSPORT_USER STAGING_DIR RUNTIME_DIR
#
# TRANSPORT_USER is the forced-command identity created by provision-target.sh (already
# granted NOPASSWD for /usr/local/libexec/pixel-release-managed). STAGING_DIR holds the
# reviewed operator files to install immutably:
#   managed.py  dispatch.py  client.py  pixel_operator.py
#   pixel_release_grammar.py  pixel-config.json
# RUNTIME_DIR is a dependency-complete runtime snapshot built by build-runtime-snapshot.mjs
# (must contain runtime-manifest.json whose SHA is bound in pixel-config.json).
#
# Security boundary: the caller-controlled STAGING_DIR is only read once. Every required
# source file is first copied into a newly created root-private staging directory (bounded,
# no-link, single-link) and its SHA-256 is bound there. The root script then executes,
# installs, and byte-compares only those root-private bytes, so a concurrent swap of a
# caller-controlled source after the copy cannot change root-executed or installed code.
#
# Every root-only step is delegated to the immutable staged pixel_operator.py so the
# provisioning harness runs the same implementation (not a shell reimplementation): staged
# validation, exact scaffolding creation, atomic versioned runtime install with Linux
# no-replace publish and retained prior versions, and durable construction of the exact
# final config bytes (runtimePath rewritten once, write-all + fsync file+parent + no-follow)
# into the root-private staging directory. The shell installs the exact updated managed
# helper (routing bundle/service/reboot/broker-bytes to pixel_operator), the dispatcher, the operator
# grammar, and the owner-side client grammar surface, then verifies staged-to-installed
# SHA-256 equality before granting the transport NOPASSWD. The exact verb grammar is
# enforced fail-closed by the immutable root helper and the dispatcher, so sudoers does not
# claim any wildcard sha precision.
#
# Test-only overrides (never set in production): PIXEL_PROVISION_TESTING=1 lets a rootless
# harness run the real script with redirected prefixes and a non-root EUID; all fixed paths
# may be redirected through the PIXEL_PROVISION_* variables. PIXEL_PROVISION_SWAP_AFTER_COPY
# (with PIXEL_PROVISION_SWAP_SOURCE) deterministically simulates a concurrent swap of a
# caller-controlled source file after the root-private copy, to prove the authorized
# snapshot is used.
[[ $EUID -eq 0 || ${PIXEL_PROVISION_TESTING:-} == 1 ]] || { echo "provision-pixel-operator.sh must run through sudo" >&2; exit 1; }
[[ $# == 3 ]] || { echo "Usage: provision-pixel-operator.sh TRANSPORT_USER STAGING_DIR RUNTIME_DIR" >&2; exit 2; }
transport=$1
staging=$2
runtime=$3
[[ "$transport" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || { echo "Unsafe transport user" >&2; exit 2; }
[[ -d "$staging" && ! -L "$staging" ]] || { echo "Staging directory is not a real directory" >&2; exit 2; }
[[ -d "$runtime" && ! -L "$runtime" ]] || { echo "Runtime snapshot is not a real directory" >&2; exit 2; }

# Redirectable prefixes (production defaults; test-only overrides for the rootless harness).
PYTHON3=${PIXEL_PROVISION_PYTHON3:-/usr/bin/python3}
INSTALL=${PIXEL_PROVISION_INSTALL:-install}
VISUDO=${PIXEL_PROVISION_VISUDO:-/usr/sbin/visudo}
STATE_DIR=${PIXEL_PROVISION_STATE_DIR:-/var/lib/pixel-release-operator}
LIBEXEC=${PIXEL_PROVISION_LIBEXEC:-/usr/local/libexec}
CONFIG_DIR=${PIXEL_PROVISION_CONFIG_DIR:-/etc/pixel-release-operator}
RUNTIME_PARENT=${PIXEL_PROVISION_RUNTIME_PARENT:-/opt/pixel-release-runtime}
SUDOERS_FILE=${PIXEL_PROVISION_SUDOERS_FILE:-/etc/sudoers.d/pixel-release-operator-bundle}
OWNER_DIR=${PIXEL_PROVISION_OWNER_DIR:-$LIBEXEC/pixel-release-owner}

config_dest=$CONFIG_DIR/pixel.json
MAX_SOURCE_BYTES=4194304
required_files="managed.py dispatch.py client.py pixel_operator.py pixel_release_grammar.py pixel-config.json"

# Ownership/mode helpers: production installs root:root with the requested mode; the
# rootless harness cannot chown, so TESTING strips the owner/group arguments only.
install_file() {
  local src=$1 dst=$2 mode=$3
  if [[ ${PIXEL_PROVISION_TESTING:-} == 1 ]]; then
    "$INSTALL" -m "$mode" "$src" "$dst"
  else
    "$INSTALL" -o root -g root -m "$mode" "$src" "$dst"
  fi
}
install_dir() {
  local dir=$1 mode=$2
  if [[ ${PIXEL_PROVISION_TESTING:-} == 1 ]]; then
    "$INSTALL" -d -m "$mode" "$dir"
  else
    "$INSTALL" -d -o root -g root -m "$mode" "$dir"
  fi
}

# Step 1: create a fresh root-private staging directory and copy every required source file
# into it, binding each file's SHA-256. Only these root-private bytes are executed or
# installed afterward; the caller-controlled staging directory is never re-read.
install_dir "$STATE_DIR" 0700
root_stage=$(mktemp -d "$STATE_DIR/provision-stage.XXXXXX")
trap 'rm -rf -- "$root_stage"' EXIT
if [[ ${PIXEL_PROVISION_TESTING:-} != 1 ]]; then
  chown root:root "$root_stage"
fi
chmod 0700 "$root_stage"
: > "$root_stage/source-manifest.txt"
for f in $required_files; do
  [[ -f "$staging/$f" && ! -L "$staging/$f" ]] || { echo "Staging is missing required file: $f" >&2; exit 2; }
  # Single hardlink and bounded size so a caller-controlled file can never alias or balloon.
  [[ $(stat -c '%h' "$staging/$f") == 1 ]] || { echo "Staging file must be single-link: $f" >&2; exit 2; }
  [[ $(stat -c '%s' "$staging/$f") -le $MAX_SOURCE_BYTES ]] || { echo "Staging file exceeds size bound: $f" >&2; exit 2; }
  cp -p "$staging/$f" "$root_stage/$f"
  sha256sum "$root_stage/$f" | awk '{print $1, "'"$f"'"}' >> "$root_stage/source-manifest.txt"
done
# Deterministic staged-source swap simulation (test-only): overwrite a caller-controlled
# source after the root-private copy to prove only the authorized snapshot is used.
if [[ ${PIXEL_PROVISION_SWAP_AFTER_COPY:-} != "" && ${PIXEL_PROVISION_TESTING:-} == 1 ]]; then
  cp -f "$PIXEL_PROVISION_SWAP_SOURCE" "$staging/$PIXEL_PROVISION_SWAP_AFTER_COPY"
fi
[[ -f "$runtime/runtime-manifest.json" && ! -L "$runtime/runtime-manifest.json" ]] || { echo "Runtime snapshot is missing runtime-manifest.json" >&2; exit 2; }
install_dir "$CONFIG_DIR" 0755
install_dir "$LIBEXEC" 0755
install_dir "$OWNER_DIR" 0755

# Step 2: root-only staged validation of the exact root-private config/runtime bytes. This
# proves the staged bytes BEFORE any install and is not part of the transport grammar.
"$PYTHON3" "$root_stage/pixel_operator.py" --validate-staging "$root_stage/pixel-config.json" "$runtime" >/dev/null

# Step 3: create the exact inbox/bundle/reboot scaffolding from the validated config.
"$PYTHON3" "$root_stage/pixel_operator.py" --provision-scaffolding "$root_stage/pixel-config.json" >/dev/null

# Step 4: atomic versioned runtime install, writing the exact final path to a root-private
# out-file (machine-safe; never shell-parsed JSON).
runtime_path_file="$root_stage/runtime-path"
"$PYTHON3" "$root_stage/pixel_operator.py" --install-runtime "$runtime" "$RUNTIME_PARENT" "$runtime_path_file" >/dev/null
new_runtime=$(cat "$runtime_path_file")
[[ -n "$new_runtime" && -d "$new_runtime" ]] || { echo "Installed runtime path is invalid" >&2; exit 1; }

# Step 5: build the exact final config bytes once into the root-private staging directory.
config_stage="$root_stage/pixel-config-final.json"
"$PYTHON3" "$root_stage/pixel_operator.py" --build-final-config "$root_stage/pixel-config.json" "$config_stage" "$new_runtime" >/dev/null

# Step 6: install the exact helper/dispatcher/grammar/client/config bytes from the
# root-private staging directory. The updated managed helper makes bundle/service/reboot/broker-bytes
# reachable; the owner-side client + grammar accept the new verbs.
install_file "$root_stage/managed.py" "$LIBEXEC/pixel-release-managed" 0755
install_file "$root_stage/dispatch.py" "$LIBEXEC/pixel-release-dispatch" 0755
install_file "$root_stage/pixel_operator.py" "$LIBEXEC/pixel_operator.py" 0644
install_file "$root_stage/pixel_release_grammar.py" "$LIBEXEC/pixel_release_grammar.py" 0644
install_file "$root_stage/client.py" "$OWNER_DIR/client.py" 0644
install_file "$root_stage/pixel_release_grammar.py" "$OWNER_DIR/pixel_release_grammar.py" 0644
install_file "$config_stage" "$config_dest" 0600

# Step 7: re-validate the installed config (root-private 0600) and the installed runtime.
"$PYTHON3" "$LIBEXEC/pixel_operator.py" --validate-config >/dev/null
"$PYTHON3" "$LIBEXEC/pixel_operator.py" --validate-runtime >/dev/null

# Step 8: verify root-private staged-to-installed SHA-256 equality for every installed
# surface before granting sudoers.
verify_equal() {
  "$PYTHON3" - "$1" "$2" <<'PY'
import hashlib, sys
a = hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest()
b = hashlib.sha256(open(sys.argv[2], "rb").read()).hexdigest()
if a != b:
    raise SystemExit(f"byte mismatch {sys.argv[1]} vs {sys.argv[2]}")
PY
}
verify_equal "$root_stage/managed.py" "$LIBEXEC/pixel-release-managed"
verify_equal "$root_stage/dispatch.py" "$LIBEXEC/pixel-release-dispatch"
verify_equal "$root_stage/pixel_operator.py" "$LIBEXEC/pixel_operator.py"
verify_equal "$root_stage/pixel_release_grammar.py" "$LIBEXEC/pixel_release_grammar.py"
verify_equal "$root_stage/client.py" "$OWNER_DIR/client.py"
verify_equal "$root_stage/pixel_release_grammar.py" "$OWNER_DIR/pixel_release_grammar.py"
verify_equal "$config_stage" "$config_dest"

# Step 9: sudoers is built in a root-private temp, validated with visudo, atomically
# installed, and revalidated. An invalid or truncated authorization file is never left in
# place. The immutable root helper and dispatcher enforce the exact grammar fail-closed, so
# sudoers grants the bare fixed helper path (no wildcard sha pattern).
sudoers_dir=$(dirname "$SUDOERS_FILE")
install_dir "$sudoers_dir" 0755
sudoers_stage=$(mktemp "$sudoers_dir/.provision-sudoers.XXXXXX")
trap 'rm -f -- "$sudoers_stage"; rm -rf -- "$root_stage"' EXIT
cat > "$sudoers_stage" <<SUDOERS
# Exact grammar is enforced by the dispatcher and the immutable root helper
# (/usr/local/libexec/pixel-release-managed); sudoers grants the bare helper path only.
$transport ALL=(root) NOPASSWD: $LIBEXEC/pixel-release-managed
SUDOERS
$VISUDO -cf "$sudoers_stage" >/dev/null
chmod 0440 "$sudoers_stage"
if [[ ${PIXEL_PROVISION_TESTING:-} != 1 ]]; then
  chown root:root "$sudoers_stage"
fi
mv -f "$sudoers_stage" "$SUDOERS_FILE"
$VISUDO -cf "$SUDOERS_FILE" >/dev/null
trap 'rm -rf -- "$root_stage"' EXIT

printf '%s\n' "transport=$transport" "authority=pixel-fixed-lifecycle-broker-bytes-only" "runtime=$new_runtime" "config=$config_dest"
