"""Shared release-operator command grammar.

The dispatcher, managed helper, and tests all validate against this single grammar so
the forced-command transport surface cannot drift from the root helper's accepted verbs.
"""

from __future__ import annotations

import re


HELPER = "/usr/local/libexec/pixel-release-managed"
SUDO = "/usr/bin/sudo"
UNITS = ("gateway", "courier")
READERS = ("ops", "frontier")
SYSTEMCTL_UNIT_VERBS = ("enable", "disable", "restart", "stop", "is-active", "is-enabled")
# Fixed bundle kinds: exact tokens, never substring inference (a "goal" token must never
# match a "fleet-goal" or "deep-work-soak" request and vice versa).
KINDS = ("goal", "fleet-goal", "deep-work-soak")
# The single lifecycle operation verb is fixed per kind; the Node lifecycle CLI is the
# semantic authority for schema, live binding, exact installed bytes, daemon-reload,
# activation, removal, and compensation.
BUNDLE_VERBS = ("install", "inspect", "status", "activate", "remove")
SERVICE_VERBS = ("status",)
BROKER_BYTES_VERBS = ("backup", "install", "restore", "verify")
SHA256 = re.compile(r"[a-f0-9]{64}")


class GrammarError(ValueError):
    pass


def validate_operation(tokens):
    """Validate a release-operator operation token list, returning it unchanged."""
    if tokens == ["status"]:
        return tokens
    if tokens == ["--validate-config"]:
        return tokens
    if (
        len(tokens) == 4
        and tokens[:2] == ["unit", "install"]
        and tokens[2] in UNITS
        and SHA256.fullmatch(tokens[3])
    ):
        return tokens
    if len(tokens) == 3 and tokens[:2] == ["unit", "remove"] and tokens[2] in UNITS:
        return tokens
    if tokens == ["systemctl", "daemon-reload"]:
        return tokens
    if (
        len(tokens) in (3, 4)
        and tokens[0] == "systemctl"
        and tokens[1] in SYSTEMCTL_UNIT_VERBS
        and tokens[2] in UNITS
    ):
        if len(tokens) == 3:
            return tokens
        if len(tokens) == 4 and tokens[3] == "--now" and tokens[1] in ("enable", "disable"):
            return tokens
    if len(tokens) == 2 and tokens[0] == "probe" and tokens[1] in READERS:
        return tokens
    if len(tokens) == 2 and tokens[0] == "broker-bytes" and tokens[1] in BROKER_BYTES_VERBS:
        return tokens
    # ---- thin lifecycle and broker-byte operator surface ----
    if (
        len(tokens) == 4
        and tokens[0] == "bundle"
        and tokens[1] in BUNDLE_VERBS
        and tokens[2] in KINDS
        and SHA256.fullmatch(tokens[3])
    ):
        return tokens
    if (
        len(tokens) == 5
        and tokens[0] == "bundle"
        and tokens[1] == "rollback"
        and tokens[2] in KINDS
        and SHA256.fullmatch(tokens[3])
        and SHA256.fullmatch(tokens[4])
    ):
        return tokens
    if (
        len(tokens) == 4
        and tokens[0] == "service"
        and tokens[1] in SERVICE_VERBS
        and tokens[2] in KINDS
        and SHA256.fullmatch(tokens[3])
    ):
        return tokens
    if (
        len(tokens) == 4
        and tokens[0] == "reboot"
        and tokens[1] == "prepare"
        and tokens[2] in ("goal", "deep-work-soak")
        and SHA256.fullmatch(tokens[3])
    ):
        return tokens
    if len(tokens) == 3 and tokens[0] == "reboot" and tokens[1] == "execute" and SHA256.fullmatch(tokens[2]):
        return tokens
    if len(tokens) == 2 and tokens[0] == "reboot" and tokens[1] in ("status", "reconcile"):
        return tokens
    raise GrammarError("release-operator command is outside the allowed grammar")
