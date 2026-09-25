"""Trusted exact-container custody for explicit preview document leases."""

import os
import re
import uuid

from preview_inspection_protocol import (
    Invalid, MAX_BUNDLE, MAX_RESULT, canonical, exact, strict_json, validate_request,
)

LABEL = "org.osmantic.ods.inspection."


def hex_value(value, length):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{" + str(length) + r"}", value)


def validate(value):
    if not isinstance(value, dict) or value.get("schemaVersion") != 2 or value.get("action") != "lease":
        raise Invalid("invalid lease request")
    operation = value.get("operation")
    base = ("schemaVersion", "action", "operation", "scope")
    if operation == "open":
        exact(value, (*base, "siteId", "sha256", "viewport"))
        # No browser assertion is performed by snapshot creation. This bounded
        # request only reuses the existing immutable byte/export validator.
        publication_request(value)
    elif operation in ("inspect", "close"):
        exact(value, (*base, "leaseId", "containerId", *(("request",) if operation == "inspect" else ())))
        if not hex_value(value["leaseId"], 32) or not hex_value(value["containerId"], 64):
            raise Invalid("invalid lease identity")
        if operation == "inspect":
            validate_request(value["request"])
    else:
        raise Invalid("invalid lease operation")
    if not hex_value(value["scope"], 64) or len(canonical(value)) > 8192:
        raise Invalid("invalid lease scope")
    return value


def publication_request(value):
    return validate_request({"schemaVersion": 1, "action": "inspect",
        "siteId": value["siteId"], "sha256": value["sha256"], "viewport": value["viewport"],
        "steps": [{"action": "assert-visible", "locator": {"selector": "html"}}]})


def labels_for(value, lease_id):
    return {LABEL + "scope": value["scope"], LABEL + "lease": lease_id,
            LABEL + "site": value["siteId"], LABEL + "sha256": value["sha256"]}


def verify_container(info, config, value):
    if not isinstance(info, dict) or info.get("Id") != value["containerId"] or info.get("Image") != config["imageId"]:
        raise Invalid("lease container identity changed")
    labels = info.get("Config", {}).get("Labels", {})
    if labels.get(LABEL + "scope") != value["scope"] or labels.get(LABEL + "lease") != value["leaseId"]:
        raise Invalid("lease container scope changed")
    expected_name = "/ods-preview-lease-" + value["leaseId"]
    if info.get("Name") != expected_name or info.get("State", {}).get("Running") is not True:
        raise Invalid("lease container is not running")
    host = info.get("HostConfig", {})
    # An owner-held ID must still identify our immutable unprivileged capsule,
    # not an owner-created container with copied labels and different authority.
    if (host.get("NetworkMode") != "none" or host.get("ReadonlyRootfs") is not True
            or host.get("Privileged") is not False or info.get("Mounts")
            or host.get("CapAdd") or set(host.get("CapDrop") or []) != {"ALL"}
            or "no-new-privileges" not in (host.get("SecurityOpt") or [])
            or host.get("Memory") != 1024 ** 3 or host.get("PidsLimit") != 128
            or info.get("Config", {}).get("User") != "65534:65534"
            or info.get("Config", {}).get("Entrypoint") != ["python3"]
            or info.get("Config", {}).get("Cmd") != ["/source/preview_inspection_lease.py", "serve"]):
        raise Invalid("lease container confinement changed")
    if value["operation"] == "inspect":
        request = value["request"]
        if labels.get(LABEL + "site") != request["siteId"] or labels.get(LABEL + "sha256") != request["sha256"]:
            raise Invalid("lease publication changed")
    return info


def handle(value, config, cancelled=None):
    import preview_inspection as broker
    validate(value)
    if os.getuid() not in (0, config["ownerUid"]):
        raise Invalid("unauthorized")
    container = None
    created = False
    completed = False
    lease_id = value.get("leaseId") or uuid.uuid4().hex
    name = "ods-preview-lease-" + lease_id
    prefix = broker.docker_prefix(config)
    try:
        if value["operation"] == "open":
            bundle = broker.export_bundle(publication_request(value), config, cancelled)
            argv = broker.capsule_argv(config, name)
            argv[argv.index("-i")] = "-d"
            position = argv.index(config["imageId"])
            additions = [part for key, val in labels_for(value, lease_id).items() for part in ("--label", key + "=" + val)]
            argv[position:position] = additions
            argv[-1] = "/source/preview_inspection_lease.py"
            argv.append("serve")
            created = True
            container = broker.bounded_process(argv, b"", timeout=10, limit=128, cancelled=cancelled).decode().strip()
            if not hex_value(container, 64):
                raise Invalid("invalid created capsule identity")
            message = {"operation": "open", "binding": {"scope": value["scope"], "leaseId": lease_id}, "bundle": bundle}
        else:
            container = value["containerId"]
            raw = broker.bounded_process([*prefix, "inspect", container], b"", timeout=5, limit=65536, cancelled=cancelled)
            infos = strict_json(raw)
            if not isinstance(infos, list) or len(infos) != 1:
                raise Invalid("invalid capsule metadata")
            verify_container(infos[0], config, value)
            message = {"operation": value["operation"], "binding": {"scope": value["scope"], "leaseId": lease_id}}
            if value["operation"] == "inspect":
                message["request"] = value["request"]
        raw = broker.bounded_process([*prefix, "exec", "-i", container, "python3", "/source/preview_inspection_lease.py", "client"],
            canonical(message), timeout=45, limit=MAX_RESULT, cancelled=cancelled)
        result = strict_json(raw)
        if (not isinstance(result, dict) or result.get("schemaVersion") != 2
                or result.get("scope") != value["scope"] or result.get("leaseId") != lease_id
                or result.get("status") != {"open": "snapshot", "inspect": "inspected", "close": "closed"}[value["operation"]]):
            raise Invalid("invalid lease receipt")
        if cancelled is not None and cancelled.is_set():
            raise Invalid("lease cancelled")
        if value["operation"] == "open":
            result["containerId"] = container
        completed = True
        return result
    finally:
        if not completed or value["operation"] == "close":
            # Only a verified existing capability or this call's fresh random
            # creation may authorize cleanup. A forged binding cannot kill it.
            may_remove = created or (container is not None and 'message' in locals())
            if may_remove:
                try:
                    broker.bounded_process([*prefix, "rm", "-f", container or name], b"", timeout=5, limit=1024)
                except (OSError, ValueError):
                    if completed:
                        raise Invalid("lease cleanup uncertain")
