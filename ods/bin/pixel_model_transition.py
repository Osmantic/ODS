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
    if action == "verify" and type(transaction_id) is str and HEX.fullmatch(transaction_id) and outcome is None:
        status, body = request("installer-model-verify", {"transaction_id": transaction_id})
        if (status != 200 or set(body) != {"mode", "pid", "config_sha256"}
                or body.get("mode") not in ("full-access", "sandboxed")
                or type(body.get("pid")) is not int or body["pid"] <= 0
                or type(body.get("config_sha256")) is not str
                or not HEX.fullmatch(body["config_sha256"])):
            raise RuntimeError("installer-model-verification-failed")
        return body
    if action in ("begin", "source-begin") and transaction_id is None and outcome is None:
        status, body = (request("installer-source-begin", {}) if action == "source-begin"
                        else request("model-begin"))
        if (status != 200 or set(body) != {"status", "transaction_id"}
                or body.get("status") != "held"
                or type(body.get("transaction_id")) is not str
                or not HEX.fullmatch(body["transaction_id"])):
            raise RuntimeError(body.get("error", "model-transition-begin-failed"))
        return body["transaction_id"]
    if action == "status" and transaction_id is None and outcome is None:
        status, body = request("model-status")
        release_completion = body.get('release_completion')
        status_body = {key: value for key, value in body.items() if key != 'release_completion'}
        valid = (body == {"pending": False} or
                 (body.get("pending") is True and
                  set(status_body) in ({"pending", "kind", "transaction_id", "phase",
                                 "configured_mode", "start_config_sha256"},
                                {"pending", "kind", "transaction_id", "phase",
                                 "configured_mode", "start_config_sha256", "error"}) and
                  body.get("kind") == "model" and
                  type(body.get("transaction_id")) is str and HEX.fullmatch(body["transaction_id"]) and
                  type(body.get("start_config_sha256")) is str and HEX.fullmatch(body["start_config_sha256"]) and
                  body.get("configured_mode") in ("full-access", "sandboxed") and
                  body.get("phase") in ("acquiring", "draining", "held", "finishing",
                                         "releasing", "native-released", "error")))
        if 'release_completion' in body:
            valid = (valid and type(release_completion) is dict
                     and set(release_completion) == {'outcome', 'config_sha256'}
                     and release_completion['outcome'] in ('apply', 'rollback')
                     and type(release_completion['config_sha256']) is str
                     and HEX.fullmatch(release_completion['config_sha256']) is not None)
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


def execute_release(action, transaction_id, *, candidate=None, digest=None, outcome=None, request=None):
    if type(transaction_id) is not str or not HEX.fullmatch(transaction_id):
        raise ValueError('invalid release transition request')
    payload = {'transaction_id': transaction_id}
    if action == 'prepare' and outcome is None:
        if (type(candidate) is not str or not candidate.startswith('/')
                or any(c in candidate for c in '\x00\n\r\t')
                or any(p in ('', '.', '..') for p in candidate.split('/')[1:])
                or type(digest) is not str or not HEX.fullmatch(digest)):
            raise ValueError('invalid release transition request')
        payload.update(candidate_path=candidate, candidate_sha256=digest)
    elif action == 'abort' and candidate is None and digest is None and outcome is None:
        pass
    elif action in ('publish', 'finish') and candidate is None and outcome in ('apply', 'rollback'):
        payload['outcome'] = outcome
        if action == 'finish':
            if type(digest) is not str or not HEX.fullmatch(digest):
                raise ValueError('invalid release transition request')
            payload['config_sha256'] = digest
        elif digest is not None:
            raise ValueError('invalid release transition request')
    else:
        raise ValueError('invalid release transition request')
    if request is None:
        request = _load_request_access()
    status, body = request('installer-release-' + action, payload)
    if action == 'abort' and status != 200 and type(body) is dict:
        recovery_errors = {
            'release-legacy-baseline-unavailable': 'Legacy release attempt has no original receipt proof; keep admission held and use reviewed manual recovery.',
            'release-prepared-recovery-required': 'Release preparation exists; abort refused. Resume the identical reviewed candidate or its verified rollback.',
            'access-release-prepared-recovery-required': 'Release preparation exists; abort refused. Resume the identical reviewed candidate or its verified rollback.',
        }
        if body.get('error') in recovery_errors:
            raise RuntimeError(recovery_errors[body['error']])
    expected = {'beforeSha', 'afterSha'} if action == 'prepare' else {'configSha256'}
    if (status != 200 or type(body) is not dict or set(body) != expected
            or any(type(body[key]) is not str or not HEX.fullmatch(body[key]) for key in expected)):
        raise RuntimeError('installer-release-transition-failed')
    return body


def main(argv):
    if len(argv) == 3 and argv[:2] == ['release-abort', '--transaction']:
        print(json.dumps(execute_release('abort', argv[2]), sort_keys=True))
        return 0
    if len(argv) == 7 and argv[:2] == ['release-prepare', '--transaction'] and argv[3] == '--candidate' and argv[5] == '--sha256':
        print(json.dumps(execute_release('prepare', argv[2], candidate=argv[4], digest=argv[6]), sort_keys=True))
        return 0
    if len(argv) == 4 and argv[:2] == ['release-publish', '--transaction']:
        print(json.dumps(execute_release('publish', argv[2], outcome=argv[3]), sort_keys=True))
        return 0
    if len(argv) == 6 and argv[:2] == ['release-finish', '--transaction'] and argv[3] == '--sha256':
        print(json.dumps(execute_release('finish', argv[2], digest=argv[4], outcome=argv[5]), sort_keys=True))
        return 0
    if len(argv) == 3 and argv[:2] == ["verify", "--transaction"]:
        print(json.dumps(execute("verify", argv[2]), separators=(",", ":"), sort_keys=True))
        return 0
    if argv == ["status"]:
        print(json.dumps(execute("status"), separators=(",", ":"), sort_keys=True))
        return 0
    if argv == ["begin"]:
        print(execute("begin"))
        return 0
    if len(argv) == 4 and argv[0] == "finish" and argv[1] == "--transaction" and argv[3] in ("applied", "rolled-back"):
        execute("finish", argv[2], argv[3])
        return 0
    raise SystemExit(
        "usage: pixel_model_transition.py status | begin | verify --transaction HEX64 | "
        "finish --transaction HEX64 applied|rolled-back | "
        "release-prepare --transaction HEX64 --candidate ABSOLUTE_PATH --sha256 HEX64 | "
        "release-publish --transaction HEX64 apply|rollback | "
        "release-abort --transaction HEX64 | "
        "release-finish --transaction HEX64 --sha256 HEX64 apply|rollback")


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
