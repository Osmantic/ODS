#!/usr/bin/env python3
"""Re-prove an unchanged Pixel access mode after an ODS-owned gateway restart."""
import json
import re
import stat
import sys
from pathlib import Path


sys.dont_write_bytecode = True
PROGRAM = Path(__file__).resolve().parent
HEX = re.compile(r"^[a-f0-9]{64}$")
DIAGNOSTIC_FIELDS = ("available", "scope", "configured_mode", "effective_mode",
                     "runtime_verified", "busy", "pending", "reason")
ERROR_CODE = re.compile(r"^[a-z][a-z0-9-]{0,95}$")


def diagnostic_projection(value):
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in DIAGNOSTIC_FIELDS:
        item = value.get(key)
        if item is None or type(item) is bool:
            result[key] = item
        elif (isinstance(item, str) and len(item) <= 96
              and all(32 <= ord(char) < 127 for char in item)):
            result[key] = item
    # The root coordinator exposes only a stable error token on failures.
    # Retain that bounded token under a distinct name so a failed proof can be
    # diagnosed without admitting arbitrary response fields or private data.
    coordinator_error = value.get("error")
    if isinstance(coordinator_error, str) and ERROR_CODE.fullmatch(coordinator_error):
        result["coordinator_error"] = coordinator_error
    return result


class ReconcileError(RuntimeError):
    def __init__(self, stage, *, status=None, projection=None):
        super().__init__(stage)
        self.stage = stage
        self.status = status if type(status) is int and 100 <= status <= 599 else None
        self.projection = diagnostic_projection(projection)

    def diagnostic(self):
        value = {"error": "pixel-access-reproof-failed", "stage": self.stage,
                 "projection": self.projection}
        if self.status is not None:
            value["httpStatus"] = self.status
        return value


def protected(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError("Pixel access recovery program custody unavailable")


for entry in (PROGRAM, *PROGRAM.parents, PROGRAM / "pixel_access_client.py"):
    protected(entry)
sys.path.insert(0, str(PROGRAM))
from pixel_access_client import request_access  # noqa: E402


def ready(value):
    mode = value.get("configured_mode")
    return (value.get("available") is True and value.get("scope") == "owner-host"
            and mode in ("sandboxed", "full-access") and value.get("effective_mode") == mode
            and value.get("runtime_verified") is True and value.get("busy") is False
            and value.get("pending") is False and value.get("reason") is None)


def recoverable_safe_transition(value):
    """Admit only the coordinator's existing sandboxed restore ceremony."""
    return (value.get("available") is True and value.get("scope") == "owner-host"
            and value.get("configured_mode") == "sandboxed"
            and value.get("effective_mode") == "unknown"
            and value.get("runtime_verified") is False and value.get("busy") is False
            and value.get("pending") is True
            and value.get("reason") == "transition-recovery-required"
            and isinstance(value.get("revision"), str)
            and HEX.fullmatch(value["revision"]))


def reconcile(request=request_access):
    status, value = request("status")
    if status != 200:
        raise ReconcileError("status-unavailable", status=status, projection=value)
    if ready(value):
        return value, False
    mode = value.get("configured_mode")
    stale_runtime_proof = (
            value.get("available") is True and value.get("scope") == "owner-host"
            and mode in ("sandboxed", "full-access") and value.get("effective_mode") == "unknown"
            and value.get("runtime_verified") is False and value.get("busy") is False
            and value.get("pending") is False and value.get("reason") == "runtime-proof-required"
            and isinstance(value.get("revision"), str) and HEX.fullmatch(value["revision"]))
    recover_safe_mode = recoverable_safe_transition(value)
    if not (stale_runtime_proof or recover_safe_mode):
        raise ReconcileError("unsafe-state", projection=value)
    status, value = request("change", {
        "mode": mode,
        "revision": value["revision"],
        # This is a same-mode reproof after an ODS-owned restart, not a new
        # privilege selection. Full-access still uses the controller's explicit
        # confirmation contract.
        # A pending recovery is admitted only for sandboxed mode above. A
        # normal same-mode full-access reproof retains explicit confirmation.
        "confirmed": mode == "full-access" and not recover_safe_mode,
    })
    if status != 200 or not ready(value):
        raise ReconcileError("change-failed", status=status, projection=value)
    return value, True


def main(request=request_access):
    try:
        value, changed = reconcile(request)
        print(json.dumps({"result": "reproved" if changed else "already-ready",
                          "mode": value["effective_mode"]}, separators=(",", ":")))
        return 0
    except ReconcileError as error:
        print(json.dumps(error.diagnostic(), separators=(",", ":"), sort_keys=True),
              file=sys.stderr)
        return 1
    except Exception as error:
        print(json.dumps({"error": "pixel-access-reproof-failed", "stage": "client-exception",
                          "exceptionType": type(error).__name__},
                         separators=(",", ":"), sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
