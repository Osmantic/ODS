"""Nonsecret runtime diagnostics. Version 1 cannot prove a release match."""
from datetime import datetime, timezone
import re

BOUNDARY = "initialization-files-not-evaluated-code-or-release-proof"
REASONS = {"partial": "release-binding-unavailable", "mismatch": "runtime-files-changed",
           "unavailable": "runtime-identity-unavailable"}
IDENTITIES = ("odsReleaseCommit", "pixelSourceRevision", "pluginSha256", "openclawVersion",
              "openclawModuleSha256", "previewImageDigest")
SCHEMAS = ("boundary", "registeredPluginToolCount", "registeredPluginToolSchemasSha256",
           "offeredToolCount", "offeredToolSchemasSha256")


def project_runtime_identity(value):
    """Accept only current partial observations; never trust an upstream green bit."""
    def nullable_hash(item):
        return item is None or isinstance(item, str) and re.fullmatch(r"[a-f0-9]{64}", item) is not None

    if not isinstance(value, dict):
        raise ValueError("invalid runtime identity")
    identity, schemas = value.get("identities"), value.get("toolSchemas")
    state, stamp = value.get("state"), value.get("observedAt")
    if (not isinstance(state, str) or state not in REASONS
            or type(value.get("schemaVersion")) is not int or value["schemaVersion"] != 1
            or value.get("reasonCode") != REASONS[state] or value.get("boundary") != BOUNDARY
            or value.get("diskComparison") not in ("match", "mismatch", "unavailable")
            or (state == "mismatch") != (value.get("diskComparison") == "mismatch")
            or value.get("runtimeMatchesRelease", True) is not (False if state == "mismatch" else None)
            or not isinstance(stamp, str)
            or re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", stamp) is None
            or not isinstance(identity, dict) or not set(IDENTITIES).issubset(identity)
            or any(identity[key] is not None for key in ("odsReleaseCommit", "pixelSourceRevision", "previewImageDigest"))
            or not all(nullable_hash(identity[key]) for key in ("pluginSha256", "openclawModuleSha256"))
            or not (identity["openclawVersion"] is None or isinstance(identity["openclawVersion"], str)
                    and re.fullmatch(r"[0-9]{4}\.[0-9]+\.[0-9]+(?:-[0-9]+)?", identity["openclawVersion"]) is not None)
            or not isinstance(schemas, dict) or not set(SCHEMAS).issubset(schemas)
            or schemas["boundary"] != "latest-created-plugin-tools-not-offered-surface"
            or type(schemas["registeredPluginToolCount"]) is not int or not 0 <= schemas["registeredPluginToolCount"] <= 64
            or not nullable_hash(schemas["registeredPluginToolSchemasSha256"])
            or (schemas["registeredPluginToolCount"] == 0) != (schemas["registeredPluginToolSchemasSha256"] is None)
            or schemas["offeredToolCount"] is not None or schemas["offeredToolSchemasSha256"] is not None):
        raise ValueError("invalid runtime identity")
    observed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if abs((datetime.now(timezone.utc) - observed).total_seconds()) > 120:
        raise ValueError("stale runtime identity")
    return {
        "schemaVersion": 1, "state": state, "diskComparison": value["diskComparison"],
        "runtimeMatchesRelease": value["runtimeMatchesRelease"], "reasonCode": REASONS[state],
        "observedAt": stamp, "boundary": BOUNDARY,
        "identities": {key: identity[key] for key in IDENTITIES},
        "toolSchemas": {key: schemas[key] for key in SCHEMAS},
    }


def unknown_runtime_identity():
    return {
        "schemaVersion": 1, "state": "unavailable", "diskComparison": "unavailable",
        "runtimeMatchesRelease": None, "reasonCode": "runtime-identity-unavailable",
        "observedAt": None, "boundary": BOUNDARY,
        "identities": dict.fromkeys(IDENTITIES),
        "toolSchemas": {"boundary": "latest-created-plugin-tools-not-offered-surface",
                        "registeredPluginToolCount": 0, "registeredPluginToolSchemasSha256": None,
                        "offeredToolCount": None, "offeredToolSchemasSha256": None},
    }

