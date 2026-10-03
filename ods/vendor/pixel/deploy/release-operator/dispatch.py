#!/usr/bin/python3
"""Forced-command dispatcher for the narrow Pixel release operator.

Invoked as the transport identity's forced command. It parses SSH_ORIGINAL_COMMAND with a
strict character allowlist (no shell, no quoting, no operators), validates the exact
release-operator grammar, and routes the single fixed root helper through
`sudo --non-interactive`. It never opens a generic SSH session and preserves no ambient
environment.
"""

from __future__ import annotations

import json
import os
import sys

from pixel_release_grammar import HELPER, SUDO, GrammarError, validate_operation

MAX_COMMAND = 32 * 1024
SAFE_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789/._- ")


class DispatchError(RuntimeError):
    pass


def tokenize(original: str) -> list[str]:
    if not original or len(original) > MAX_COMMAND or "\x00" in original:
        raise DispatchError("SSH command is empty, oversized, or contains a NUL byte")
    if any(character not in SAFE_CHARS for character in original):
        raise DispatchError("SSH command contains a character outside the release-operator grammar")
    tokens = original.split()
    if not tokens or any(not token for token in tokens):
        raise DispatchError("SSH command is empty")
    return tokens


def clean_environment() -> dict[str, str]:
    return {
        "HOME": "/",
        "LANG": "C.UTF-8",
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    }


def main() -> int:
    testing = os.environ.get("PIXEL_RELEASE_DISPATCH_TESTING") == "1" and getattr(os, "geteuid", lambda: 1)() != 0
    tokens = tokenize(os.environ.get("SSH_ORIGINAL_COMMAND", ""))
    if tokens[0] != HELPER:
        raise DispatchError("transport command must invoke the fixed release-operator helper")
    operation = validate_operation(tokens[1:])
    routed = [SUDO, "--non-interactive", HELPER, *operation]
    if testing:
        print(json.dumps({
            "shell": False,
            "transportIdentitySeparated": True,
            "helper": HELPER,
            "operation": operation,
            "routedArgv": routed,
        }, sort_keys=True))
        return 0
    os.chdir("/")
    os.execvpe(routed[0], routed, clean_environment())
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DispatchError, GrammarError, OSError) as error:
        print(f"pixel-release-dispatch: {error}", file=sys.stderr)
        raise SystemExit(126)
