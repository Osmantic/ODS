#!/usr/bin/env python3
"""Fixed client for the root-owned Pixel model transition."""
import json
import re
import stat
import sys
from pathlib import Path


sys.dont_write_bytecode = True
PROGRAM = Path(__file__).resolve().parent

HEX = re.compile(r"[a-f0-9]{64}\Z")


def protected(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError("Pixel model transition program custody unavailable")


def _load_request_access():
    # Python isolated mode deliberately excludes the script directory from
    # sys.path. Admit only the installed, root-protected helper directory so a
    # checkout or user-writable module can never satisfy this privileged client.
    for entry in (PROGRAM, *PROGRAM.parents, PROGRAM / "pixel_access_client.py"):
        protected(entry)
    sys.path.insert(0, str(PROGRAM))
    from pixel_access_client import request_access  # noqa: E402
    return request_access


def execute(action, transaction_id=None, outcome=None, *, request=None):
    if request is None:
        request = _load_request_access()
    if action == "begin" and transaction_id is None and outcome is None:
        status, body = request("model-begin")
        if (status != 200 or set(body) != {"status", "transaction_id"}
                or body.get("status") != "held"
                or type(body.get("transaction_id")) is not str
                or not HEX.fullmatch(body["transaction_id"])):
            raise RuntimeError(body.get("error", "model-transition-begin-failed"))
        return body["transaction_id"]
    if action == "status" and transaction_id is None and outcome is None:
        status, body = request("model-status")
        valid = (body == {"pending": False} or
                 (body.get("pending") is True and
                  set(body) in ({"pending", "kind", "transaction_id", "phase",
                                 "configured_mode", "start_config_sha256"},
                                {"pending", "kind", "transaction_id", "phase",
                                 "configured_mode", "start_config_sha256", "error"}) and
                  body.get("kind") == "model" and
                  type(body.get("transaction_id")) is str and HEX.fullmatch(body["transaction_id"]) and
                  type(body.get("start_config_sha256")) is str and HEX.fullmatch(body["start_config_sha256"]) and
                  body.get("configured_mode") in ("full-access", "sandboxed") and
                  body.get("phase") in ("acquiring", "draining", "held", "finishing",
                                         "releasing", "native-released", "error")))
        if status != 200:
            error = body.get("error")
            if type(error) is str and re.fullmatch(r"[a-z][a-z0-9-]{0,95}", error):
                raise RuntimeError(error)
            raise RuntimeError("model-transition-status-failed")
        if not valid:
            # A malformed success response must never become a diagnostic echo
            # of a journal token or other root-private field.
            raise RuntimeError("model-transition-status-failed")
        return body
    if (action == "finish" and type(transaction_id) is str and HEX.fullmatch(transaction_id)
            and outcome in ("applied", "rolled-back")):
        status, body = request("model-finish", {
            "transaction_id": transaction_id, "outcome": outcome})
        if status != 200 or body != {"status": "released", "outcome": outcome}:
            raise RuntimeError(body.get("error", "model-transition-finish-failed"))
        return body["status"]
    raise ValueError("invalid model transition request")


def main(argv):
    if argv == ["status"]:
        print(json.dumps(execute("status"), separators=(",", ":"), sort_keys=True))
        return 0
    if argv == ["begin"]:
        print(execute("begin"))
        return 0
    if len(argv) == 4 and argv[0] == "finish" and argv[1] == "--transaction" and argv[3] in ("applied", "rolled-back"):
        execute("finish", argv[2], argv[3])
        return 0
    raise SystemExit("usage: pixel_model_transition.py status | begin | finish --transaction HEX64 applied|rolled-back")


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
