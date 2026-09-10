"""Fixed owner-worker pipe frames, not public settings or privilege authority."""
import json
import math
from pathlib import PurePosixPath
import re
import uuid

MAX_REQUEST = 16384
MAX_REPLY = 8192
BASE = {"operation", "openclaw", "config_sha256", "confirmed"}
KEYS = {"status": BASE, "full-access": BASE, "sandboxed": BASE, "settings-status": BASE,
        "settings-apply": BASE | {"transaction_id", "settings_revision", "preferences", "capabilities"},
        "settings-recover": BASE | {"transaction_id"}, "provider-status": BASE,
        "provider-worker-status": BASE | {"provider_probe"},
        "provider-change": BASE | {"transaction_id", "binding"},
        "provider-recover": BASE | {"transaction_id"}}
HOOKS = {"status": (), "full-access": ("busy", "restart"), "sandboxed": ("busy", "restart"),
         "settings-status": (), "settings-apply": ("busy", "settings-activate"),
         "settings-recover": ("busy", "settings-activate"), "provider-status": (), "provider-worker-status": (),
         "provider-change": ("busy", "provider-activate"), "provider-recover": ("busy", "provider-activate")}
HEX = re.compile(r"[a-f0-9]{64}\Z")


class ProtocolError(ValueError):
    pass


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ProtocolError("owner-protocol-failed")
        result[key] = value
    return result


def _finite(value):
    if not math.isfinite(float(value)):
        raise ProtocolError("owner-protocol-failed")
    return float(value)


def decode_frame(raw, maximum):
    try:
        if type(raw) is not str or not raw.endswith("\n") or len(raw.encode("utf-8")) > maximum:
            raise ProtocolError("owner-protocol-failed")
        return json.loads(raw, object_pairs_hook=_pairs, parse_constant=_finite, parse_float=_finite)
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise ProtocolError("owner-protocol-failed") from None


def read_frame(stream, maximum):
    return decode_frame(stream.readline(maximum + 1), maximum)


def control_request(value):
    """Fixed root socket operations; no paths or caller-supplied capabilities."""
    if type(value) is not dict or type(value.get("operation")) is not str:
        raise ProtocolError("invalid-request")
    operation = value["operation"]
    keys = {"status": {"operation"}, "change": {"operation", "request"},
            "settings-status": {"operation", "data_dir_id"},
            "settings-change": {"operation", "data_dir_id", "request"},
            "provider-status": {"operation", "data_dir_id"},
            "provider-change": {"operation", "data_dir_id", "request"}}
    if operation not in keys or set(value) != keys[operation]:
        raise ProtocolError("invalid-request")
    if operation.startswith(("settings-", "provider-")) and (type(value["data_dir_id"]) is not str or not HEX.fullmatch(value["data_dir_id"])):
        raise ProtocolError("invalid-request")
    if "request" in value and type(value["request"]) is not dict:
        raise ProtocolError("invalid-request")
    return value


def request(value):
    if type(value) is not dict or type(value.get("operation")) is not str:
        raise ProtocolError("owner-protocol-failed")
    operation = value["operation"]
    allowed = KEYS.get(operation)
    if allowed is None or set(value) not in (allowed, allowed | {'expected_projection'} if operation == 'provider-change' else allowed):
        raise ProtocolError("owner-protocol-failed")
    if (type(value["openclaw"]) is not str or not PurePosixPath(value["openclaw"]).is_absolute()
            or "\x00" in value["openclaw"] or type(value["confirmed"]) is not bool):
        raise ProtocolError("owner-protocol-failed")
    if operation.endswith("status"):
        if value["config_sha256"] is not None:
            raise ProtocolError("owner-protocol-failed")
    elif type(value["config_sha256"]) is not str or not HEX.fullmatch(value["config_sha256"]):
        raise ProtocolError("owner-protocol-failed")
    if operation == 'provider-worker-status':
        probe = value['provider_probe']
        if (type(probe) is not dict or set(probe) != {'python', 'launcher', 'providerDirectory', 'receipt'}
                or type(probe['receipt']) is not dict
                or any(type(probe[key]) is not str or '\x00' in probe[key]
                       or not PurePosixPath(probe[key]).is_absolute()
                       for key in ('python', 'launcher', 'providerDirectory'))
                or PurePosixPath(probe['launcher']).name != 'ods-pixel-route-lease'):
            raise ProtocolError('owner-protocol-failed')
    if operation in ("settings-apply", "settings-recover", "provider-change", "provider-recover"):
        if type(value["transaction_id"]) is not str or not HEX.fullmatch(value["transaction_id"]):
            raise ProtocolError("owner-protocol-failed")
    if operation == "settings-apply" and (
            type(value["settings_revision"]) is not int or not 0 <= value["settings_revision"] <= 2**53 - 1
            or type(value["preferences"]) is not dict or type(value["capabilities"]) is not dict):
        raise ProtocolError("owner-protocol-failed")
    if operation == "provider-change":
        provider_binding(value["binding"])
        if 'expected_projection' in value:
            projection = value['expected_projection']
            if (type(projection) is not dict or set(projection) != {'afterSha', 'previousPlanSha'}
                    or type(projection['afterSha']) is not str or not HEX.fullmatch(projection['afterSha'])
                    or projection['previousPlanSha'] is not None and
                    (type(projection['previousPlanSha']) is not str or not HEX.fullmatch(projection['previousPlanSha']))):
                raise ProtocolError('owner-protocol-failed')
    return value


def provider_binding(value):
    if value is None:
        return
    if (type(value) is not dict or set(value) != {"schemaVersion", "activationId", "revision", "allowCloud"}
            or type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1
            or type(value["revision"]) is not int or not 0 <= value["revision"] < 2**53
            or type(value["allowCloud"]) is not bool or type(value["activationId"]) is not str):
        raise ProtocolError("owner-protocol-failed")
    try:
        if str(uuid.UUID(value["activationId"])) != value["activationId"]:
            raise ValueError()
    except ValueError:
        raise ProtocolError("owner-protocol-failed") from None


def hook_reply(operation, name, value):
    if name not in HOOKS.get(operation, ()):
        raise ProtocolError("owner-protocol-failed")
    if name in ("settings-activate", "provider-activate"):
        valid = type(value) is str and value in ("verified", "rejected", "unavailable")
    else:
        valid = type(value) is bool
    if not valid:
        raise ProtocolError("host-hook-failed")
    return value


def result(operation, value):
    if type(value) is not dict:
        raise ProtocolError("owner-protocol-failed")
    if operation == 'provider-worker-status':
        if set(value) != {'ready'} or type(value['ready']) is not bool:
            raise ProtocolError('owner-protocol-failed')
        return value
    if operation.startswith("provider-"):
        return provider_result(operation, value)
    if not operation.startswith("settings-"):
        return value  # Existing access status contract is interpreted by inspect.
    def revision(item):
        return type(item) is int and 0 <= item <= 2**53 - 1
    def checksum(item):
        return type(item) is str and HEX.fullmatch(item) is not None
    valid = checksum(value.get("configSha256"))
    if operation == "settings-status":
        valid = valid and set(value) == {"configSha256", "managedRevision", "pending", "completion", "runtimeVerified"}
        if not valid:
            raise ProtocolError("owner-protocol-failed")
        valid = (value["runtimeVerified"] is False and type(value["pending"]) is bool
                 and (value["managedRevision"] is None or revision(value["managedRevision"])))
        completed = value["completion"]
        if completed is not None:
            valid = (valid and not value["pending"] and type(completed) is dict
                     and set(completed) == {"transactionId", "settingsRevision", "outcome", "configSha256"}
                     and checksum(completed["transactionId"]) and checksum(completed["configSha256"])
                     and revision(completed["settingsRevision"]) and completed["outcome"] in ("applied", "rolled-back"))
    elif value.get("status") == "rolled-back":
        valid = valid and set(value) == {"status", "configSha256"}
    else:
        valid = (valid and operation == "settings-apply" and value.get("status") == "runtime-verified"
                 and set(value) == {"status", "settingsRevision", "configSha256"} and revision(value["settingsRevision"]))
    if not valid:
        raise ProtocolError("owner-protocol-failed")
    return value


def provider_result(operation, value):
    def checksum(item):
        return type(item) is str and HEX.fullmatch(item) is not None
    provider_binding(value.get("binding"))
    valid = checksum(value.get("configSha256"))
    if operation == "provider-status":
        valid = (valid and set(value) == {"configSha256", "binding", "pending", "completion", "runtimeVerified"}
                 and type(value["pending"]) is bool and value["runtimeVerified"] is False)
        completed = value.get("completion")
        if completed is not None:
            valid = (valid and not value["pending"] and type(completed) is dict
                     and set(completed) == {"transactionId", "binding", "outcome", "configSha256"}
                     and checksum(completed["transactionId"]) and checksum(completed["configSha256"])
                     and completed["outcome"] in ("applied", "rolled-back"))
            if valid:
                provider_binding(completed["binding"])
    else:
        valid = (valid and set(value) == {"status", "binding", "configSha256"}
                 and (value.get("status") == "rolled-back" or
                      operation == "provider-change" and value.get("status") == "registration-verified"))
    if not valid:
        raise ProtocolError("owner-protocol-failed")
    return value
