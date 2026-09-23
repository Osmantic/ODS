"""Observational readiness only; v1 cannot establish a verified release."""
from datetime import datetime, timezone


def project_readiness(route_available, access, identity, access_issue="access-proof-unverified"):
    """Combine validated access/identity projections without granting admission.

    Access is the existing PixelAccessStatus projection, not a configuration
    assertion. No arbitrary upstream strings or private receipt fields survive.
    """
    access_state, mode = "unverified", "unknown"
    reason = access_issue if access_issue in {
        "access-probe-timeout", "access-probe-unavailable", "access-probe-invalid",
    } else "access-proof-unverified"
    if access is not None:
        if access["pending"]:
            access_state, reason = "transitioning", "access-transition-pending"
        elif not access["available"]:
            access_state = "failed"
            reason = "access-inspection-failed" if access["reason"] == "inspection-failed" else "access-verification-failed"
        elif access["runtime_verified"]:
            access_state, mode, reason = "verified", access["effective_mode"], "release-binding-unavailable"
        # Busy means activity, not failed proof. A verified busy runtime stays
        # verified for access only; it still cannot prove release readiness.
    release_state = "mismatch" if identity["runtimeMatchesRelease"] is False else "unverified"
    if release_state == "mismatch" and access_state not in {"failed", "transitioning"}:
        reason = "runtime-files-changed"
    state = "attention" if access_state in {"failed", "transitioning"} or release_state == "mismatch" else "unverified"
    if not route_available:
        state, reason = "unavailable", "model-route-unavailable"
    return {
        "schemaVersion": 1, "state": state, "routeAvailable": route_available,
        "accessState": access_state, "effectiveMode": mode,
        "releaseState": release_state, "reasonCode": reason,
        "observedAt": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    }
