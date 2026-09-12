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


def reconcile(request=request_access):
    status, value = request("status")
    if status != 200:
        raise RuntimeError("Pixel access status unavailable")
    if ready(value):
        return value, False
    mode = value.get("configured_mode")
    if not (value.get("available") is True and value.get("scope") == "owner-host"
            and mode in ("sandboxed", "full-access") and value.get("effective_mode") == "unknown"
            and value.get("runtime_verified") is False and value.get("busy") is False
            and value.get("pending") is False and value.get("reason") == "runtime-proof-required"
            and isinstance(value.get("revision"), str) and HEX.fullmatch(value["revision"])):
        raise RuntimeError("Pixel access state is not safe for automatic reproof")
    status, value = request("change", {
        "mode": mode,
        "revision": value["revision"],
        # This is a same-mode reproof after an ODS-owned restart, not a new
        # privilege selection. Full-access still uses the controller's explicit
        # confirmation contract.
        "confirmed": mode == "full-access",
    })
    if status != 200 or not ready(value):
        raise RuntimeError("Pixel access runtime reproof failed")
    return value, True


def main():
    try:
        value, changed = reconcile()
        print(json.dumps({"result": "reproved" if changed else "already-ready",
                          "mode": value["effective_mode"]}, separators=(",", ":")))
        return 0
    except Exception:
        print("Pixel access runtime could not be safely re-proved", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
