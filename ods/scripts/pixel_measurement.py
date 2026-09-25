"""Deterministic offline schedule and privacy-safe measurement joins.

This module does not dispatch, install, restore, acquire lanes, or infer cache
temperature. The supervisor must separately prove all admission conditions.
"""
import hashlib
import json
import random


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def paired_schedule(seed, task, checkpoint, identities, candidate):
    """Six blocks, three orders each; byte/mode checkpoint shared within pairs."""
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("bounded explicit random seed required")
    required = {"source", "runtime", "model", "serving", "capabilities", "oracle"}
    if not isinstance(identities, dict) or set(identities) != required:
        raise ValueError("exact source/runtime/model/serving/capability/oracle identities required")
    if any(not isinstance(v, str) or len(v) != 64 or any(c not in '0123456789abcdef' for c in v)
           for v in identities.values()):
        raise ValueError("identities must be exact SHA256 receipts")
    if not isinstance(checkpoint, dict) or set(checkpoint) != {"files", "history"}:
        raise ValueError("files including modes and history checkpoint required")
    if not isinstance(checkpoint["history"], str) or len(checkpoint["history"]) != 64 or any(c not in '0123456789abcdef' for c in checkpoint["history"]):
        raise ValueError("exact history checkpoint receipt required")
    if not isinstance(checkpoint["files"], list):
        raise ValueError("file manifest required, including explicit empty workspace")
    paths = set()
    for entry in checkpoint["files"]:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "mode"}:
            raise ValueError("exact file path/hash/mode required")
        name, sha, mode = entry["path"], entry["sha256"], entry["mode"]
        if not isinstance(name, str) or not name or "\\" in name or ":" in name or any(p in {"", ".", ".."} for p in name.split("/")) or name in paths:
            raise ValueError("unique relative POSIX file path required")
        if not isinstance(sha, str) or len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha) or type(mode) is not int or mode < 0 or mode > 0o777:
            raise ValueError("exact file bytes and original permission bits required")
        paths.add(name)
    if not isinstance(task, dict) or not task or candidate not in {"prompt-only", "tool-progress", "combined"}:
        raise ValueError("task and reviewed candidate arm required")
    orders = [("baseline", candidate)]*3 + [(candidate, "baseline")]*3
    random.Random(seed).shuffle(orders)
    blocks = []
    for index, order in enumerate(orders, 1):
        # Measurement IDs exist only in the manifest, never appended to prompts.
        blocks.append({"block": index, "arms": list(order), "taskSha256": digest(task),
                       "checkpointSha256": digest(checkpoint), "cacheCondition": "unverified",
                       "promptMutation": False, "freezeFailedAttempts": True})
    return {"schema": "ods.pixel-paired-schedule.v1", "seed": seed, "identities": identities,
            "blocks": blocks, "launchAuthorized": False,
            "requires": ["actual-physical-lock", "fresh-source-model-binding", "retained-native-backend-idle",
                         "checkpoint-bytes-and-modes", "same-logical-cache-state", "same-output-allowance",
                         "no-competing-work", "root-reviewed-request-lease", "post-attempt-settlement"]}


def join_attempts(native, router):
    """Exact correlation join only; one provider HTTP call may cause repair sends."""
    index = {}
    for row in native:
        key = row.get("correlationId")
        if not isinstance(key, str) or key in index:
            raise ValueError("missing or duplicate native HTTP correlation")
        index[key] = row
    result = []
    for row in router:
        peer = index.get(row.get("correlationId")) if row.get("binding") == "signed-http-header" else None
        result.append({"join": "exact" if peer else "unavailable", "modelCallId": peer.get("modelCallId") if peer else None,
                       "nativeFetchAttempt": peer.get("attempt") if peer else None,
                       "routerSendAttempt": row.get("attempt"), "repairReason": row.get("repairReason"),
                       "routerAdmissionMs": row.get("routerAdmissionMs"), "routeReadyWaitMs": row.get("routeReadyWaitMs"),
                       "upstreamElapsedMs": row.get("elapsedMs"), "firstContentDeltaMs": row.get("firstContentDeltaMs"),
                       "responseMetrics": row.get("responseMetrics"), "status": row.get("status"),
                       "backendQueueMs": None, "usefulVisibleProgressMs": None})
    return result


def first_component_difference(left, right, *, same_scope):
    """Locate hashed components, never claim byte/token prefix offsets."""
    if not same_scope or left.get("state") != "observed" or right.get("state") != "observed":
        return {"state": "unavailable", "tokenOffset": None}
    for group in ("tools", "messages"):
        a, b = left["components"][group], right["components"][group]
        if a["digest"] != b["digest"]:
            for index, (x, y) in enumerate(zip(a["entries"], b["entries"])):
                if x != y:
                    return {"state": "different", "component": group, "index": index, "tokenOffset": None}
            return {"state": "different", "component": group, "index": min(len(a["entries"]), len(b["entries"])), "tokenOffset": None}
    return {"state": "same-observed-components", "tokenOffset": None}
