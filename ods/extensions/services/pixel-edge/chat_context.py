"""Closed public projection for context status; no session text crosses here."""
import re


def _text(value, maximum, pattern=r"^[^\x00-\x1f\x7f]+$"):
    return isinstance(value, str) and value == value.strip() and 0 < len(value) <= maximum and re.fullmatch(pattern, value) is not None


def _number(value, maximum=100_000_000):
    return type(value) is int and 0 <= value <= maximum


def project_context(value):
    if not isinstance(value, dict) or type(value.get("schemaVersion")) is not int or value["schemaVersion"] != 1:
        raise ValueError("invalid context")
    if value.get("status") not in {"ready", "missing", "busy", "unavailable"}:
        raise ValueError("invalid status")
    revision = value.get("sessionRevision")
    if revision is not None and not _text(revision, 160, r"^[A-Za-z0-9:_-]+$"):
        raise ValueError("invalid revision")
    result = {"schemaVersion": 1, "status": value["status"], "sessionRevision": revision, "context": None, "model": None}
    context, model = value.get("context"), value.get("model")
    if context is not None:
        if (not isinstance(context, dict) or not _number(context.get("used"))
                or not _number(context.get("window"), 10_000_000) or context["window"] < 1
                or not _text(context.get("measuredAt"), 64, r"^[0-9TZ: .+\-]+$")):
            raise ValueError("invalid context measurement")
        result["context"] = {key: context[key] for key in ("used", "window", "measuredAt")}
    if model is not None:
        if (not isinstance(model, dict) or not _text(model.get("id"), 512) or not _text(model.get("provider"), 128)
                or not _number(model.get("contextWindow"), 10_000_000) or model["contextWindow"] < 1
                or context is not None and context["window"] != model["contextWindow"]):
            raise ValueError("invalid model")
        result["model"] = {key: model[key] for key in ("id", "provider", "contextWindow")}
        if "routeFingerprint" in model:
            if not _text(model["routeFingerprint"], 64, r"^[a-f0-9]{64}$"):
                raise ValueError("invalid model route")
            result["model"]["routeFingerprint"] = model["routeFingerprint"]
    compact = value.get("compaction")
    if (not isinstance(compact, dict) or compact.get("status") not in {"idle", "running", "completed", "skipped", "failed", "unknown"}
            or not _number(compact.get("count"))):
        raise ValueError("invalid compaction")
    result["compaction"] = {key: compact[key] for key in ("status", "count")}
    for key in ("requestId", "reason", "tokensBefore", "tokensAfter"):
        item = compact.get(key)
        if item is None:
            continue
        valid = (_text(item, 128, r"^[A-Za-z0-9_-]+$") if key == "requestId" else
                 _text(item, 96, r"^[a-z][a-z0-9-]*$") if key == "reason" else _number(item))
        if not valid:
            raise ValueError("invalid compaction detail")
        result["compaction"][key] = item
    history = value.get("history")
    if (not isinstance(history, dict) or history.get("status") not in {"ready", "pending", "unknown"}
            or not _number(history.get("acknowledgedMessages"), 2000)
            or history.get("revision") is not None and not _text(history["revision"], 64, r"^[a-f0-9]{64}$")):
        raise ValueError("invalid history")
    result["history"] = {key: history[key] for key in ("status", "revision", "acknowledgedMessages")}
    if history.get("reason") is not None:
        if not _text(history["reason"], 96, r"^[a-z][a-z0-9-]*$"):
            raise ValueError("invalid history reason")
        result["history"]["reason"] = history["reason"]
    return result


def valid_history_snapshot(data):
    snapshot = data.get("history_snapshot")
    if snapshot is None:
        return True  # Existing OpenAI-compatible clients remain supported.
    if (not _text(data.get("request_id"), 128, r"^[A-Za-z0-9_-]+$")
            or not isinstance(snapshot, dict) or set(snapshot) != {"schemaVersion", "messages"}
            or type(snapshot["schemaVersion"]) is not int or snapshot["schemaVersion"] != 1
            or not isinstance(snapshot["messages"], list) or not 1 <= len(snapshot["messages"]) <= 2000):
        return False
    size = 0
    for message in snapshot["messages"]:
        if (not isinstance(message, dict) or set(message) != {"role", "content"}
                or message["role"] not in {"user", "assistant"} or not isinstance(message["content"], str)):
            return False
        size += len(message["content"].encode("utf-8"))
        if size > 4 * 1024 * 1024:
            return False
    return bool(data.get("messages")) and snapshot["messages"][-1] == data["messages"][-1] and snapshot["messages"][-1]["role"] == "user"
