#!/usr/bin/env bash
# install-owner-test-key.sh — thin adapter to the hardened Node credential ingress
#
# This script is a strict-grammar adapter that delegates ALL credential
# file-creation authority to the Node CLI. It never writes, creates dirs,
# stats, unlinks, hashes, logs, or places credential bytes in argv or env.
#
# The Node core writes to a staging file first, verifies content,
# then atomically renames to the final path.  On failure before publication,
# the staging inode is zeroed and truncated via its file descriptor.
# Staging names are random and do not block subsequent create-only attempts.
#
# Usage:
#   # Pipe a credential from a trusted producer to the wrapper.
#   # The producer must be a trusted process that writes the credential
#   # directly to stdout. Credential bytes must never appear in shell
#   # variables, argv, or environment.
#     your-trusted-key-producer | ./install-owner-test-key.sh --provider moonshot-kimi --directory /path/to/cred-dir
#
#   # Legacy compat (deprecated; maps to --provider moonshot-kimi):
#     ./install-owner-test-key.sh /path/to/cred-dir
#
# Constraints:
#   - Provider id must be in the closed allowlist: moonshot-kimi, openai, anthropic
#   - Credential bytes flow through stdin pipe only; never in argv, stdout, stderr,
#     or environment variables
#   - TTY stdin is rejected — credential must come from a pipe, not an interactive terminal
#   - The Node CLI is the sole file-creation authority
#   - No recursive parent creation; no cleanup rm; no credential handling in shell
set -euo pipefail

fail() { printf '%s\n' "Pixel provider credential ingress failed closed." >&2; exit 70; }

# ---------------------------------------------------------------------------
# Reject TTY stdin: credentials must come from a pipe, not a terminal
# ---------------------------------------------------------------------------
if [ -t 0 ]; then
  printf '%s\n' "Pixel provider credential ingress failed closed: stdin is a terminal; pipe credential data to stdin." >&2
  exit 70
fi

# ---------------------------------------------------------------------------
# Locate the Node ingress CLI (same directory as this script)
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INGRESS_CLI="${SCRIPT_DIR}/provider-credential-ingress.mjs"

if [[ ! -f "$INGRESS_CLI" ]]; then
  printf '%s\n' "Credential ingress CLI not found at ${INGRESS_CLI}" >&2
  exit 70
fi

# ---------------------------------------------------------------------------
# Strict argument parsing — reject before invoking Node
# ---------------------------------------------------------------------------
PROVIDER=""
DIRECTORY=""

if [[ $# -eq 1 ]]; then
  # Legacy compat: single positional argument is the credential directory
  if [[ "$1" == /* && "$1" != "/" ]]; then
    PROVIDER="moonshot-kimi"
    DIRECTORY="$1"
  else
    fail
  fi
elif [[ $# -ge 2 ]]; then
  # Flag-based parsing with strict grammar
  SEEN_PROVIDER=0
  SEEN_DIRECTORY=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --provider)
        if [[ $SEEN_PROVIDER -eq 1 ]]; then fail; fi
        if [[ $# -lt 2 ]]; then fail; fi
        PROVIDER="$2"; SEEN_PROVIDER=1; shift 2
        ;;
      --directory)
        if [[ $SEEN_DIRECTORY -eq 1 ]]; then fail; fi
        if [[ $# -lt 2 ]]; then fail; fi
        DIRECTORY="$2"; SEEN_DIRECTORY=1; shift 2
        ;;
      *)
        fail
        ;;
    esac
  done
  if [[ -z "$PROVIDER" || -z "$DIRECTORY" ]]; then fail; fi
  if [[ "$DIRECTORY" != /* || "$DIRECTORY" == "/" ]]; then fail; fi
else
  fail
fi

# Validate provider is in the closed allowlist
case "$PROVIDER" in
  moonshot-kimi|openai|anthropic) ;;
  *) fail ;;
esac

# ---------------------------------------------------------------------------
# Delegate to the Node CLI — credential bytes flow through stdin pipe only
# ---------------------------------------------------------------------------
exec node "$INGRESS_CLI" --provider "$PROVIDER" --directory "$DIRECTORY"
