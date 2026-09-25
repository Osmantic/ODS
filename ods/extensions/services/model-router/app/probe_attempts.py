"""Opt-in, signed-probe metadata only. No request bodies are retained."""
from collections import OrderedDict
import hashlib
import hmac
import json
import re
import time


class ProbeAttempts:
    MAX_SCOPES = 32
    MAX_ATTEMPTS = 16
    MAX_TTL = 1800
    MAX_BODY = 2 * 1024 * 1024
    MAX_MESSAGES = 256

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.scopes = OrderedDict()

    def _scope(self, probe_id, signing_key=None):
        scope = self.scopes.get(probe_id)
        if scope is not None and self.clock() >= scope["expires"]:
            self.scopes.pop(probe_id, None)
            return None
        if scope is not None and signing_key is not None:
            fingerprint = hashlib.sha256(signing_key.encode()).digest()
            if not signing_key or not hmac.compare_digest(fingerprint, scope["keyFingerprint"]):
                return None
        return scope

    def arm(self, probe_id, ttl, limit, signing_key):
        if (not re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", probe_id)
                or type(ttl) is not int or not 1 <= ttl <= self.MAX_TTL
                or type(limit) is not int or not 1 <= limit <= self.MAX_ATTEMPTS
                or not signing_key):
            raise ValueError("Invalid capture lease")
        if self._scope(probe_id) is not None:
            raise FileExistsError("Capture lease already exists")
        now = self.clock()
        # Authentication keys are not stored in the diagnostic scope. The derived
        # key prevents low-entropy message hashes from becoming a public lookup.
        key = hmac.new(signing_key.encode(), b"ods.probe-attempt.v1\0" + probe_id.encode(),
                       hashlib.sha256).digest()
        self.scopes[probe_id] = {"expires": now + ttl, "limit": limit, "records": [],
            "key": key, "keyFingerprint": hashlib.sha256(signing_key.encode()).digest()}
        while len(self.scopes) > self.MAX_SCOPES:
            self.scopes.popitem(last=False)
        return {"schemaVersion": 1, "probeId": probe_id, "ttlSeconds": ttl,
                "maxAttempts": limit, "rawPayloadRetention": False}

    @staticmethod
    def _fingerprint(value, key):
        raw = json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                         separators=(",", ":")).encode("ascii")
        return {"bytes": len(raw), "digest": hmac.new(key, raw, hashlib.sha256).hexdigest()}

    def begin(self, probe_id, signing_key, request_id, attempt, body, route):
        """Caller has already authenticated the signed marker in this request."""
        scope = self._scope(probe_id, signing_key)
        if scope is None or len(scope["records"]) >= scope["limit"]:
            return None
        row = {"requestId": request_id, "attempt": attempt,
               "sequence": len(scope["records"]) + 1,
               "startedMonotonic": self.clock(), "elapsedMs": None,
               "status": "pending", "httpStatus": None,
               "boundary": "actual-router-upstream-send-not-tool-execution",
               "encoding": "hmac-sha256;components=json-sort-keys-ascii-v1",
               "routeSeq": route["routeSeq"], "routedModel": route["runtimeModelId"],
               "endpointId": route["endpointId"], "backend": route["backendKind"],
               "fingerprints": {"state": "unavailable"}}
        scope["records"].append(row)
        try:
            if type(body) is not bytes or len(body) > self.MAX_BODY:
                return probe_id, scope, row
            payload = json.loads(body)
            if type(payload) is not dict:
                return probe_id, scope, row
            messages = payload.get("messages", [])
            tools = payload.get("tools", [])
            if (type(messages) is not list or len(messages) > self.MAX_MESSAGES
                    or type(tools) is not list or len(tools) > 256):
                return probe_id, scope, row
            key = scope["key"]
            fingerprint = lambda value: self._fingerprint(value, key)
            components = {name: fingerprint({"present": name in payload, "value": payload.get(name)})
                          for name in ["messages", "tools", "input", "prompt"]}
            components["messages"]["entries"] = []
            for index, message in enumerate(messages):
                if type(message) is not dict:
                    raise ValueError("Invalid message structure")
                role = message.get("role")
                components["messages"]["entries"].append({"index": index,
                    "role": role if role in {"system", "developer", "user", "assistant", "tool", "function"} else "other",
                    "message": fingerprint(message),
                    "content": fingerprint({"present": "content" in message, "value": message.get("content")}),
                    "toolCalls": fingerprint({"present": "tool_calls" in message, "value": message.get("tool_calls")})})
            components["tools"]["entries"] = [fingerprint(tool) for tool in tools]
            row["fingerprints"] = {"state": "observed", "bodyBytes": len(body),
                "bodyDigest": hmac.new(key, body, hashlib.sha256).hexdigest(),
                "components": components}
        except (TypeError, ValueError, RecursionError, UnicodeError):
            pass
        finally:
            ready = self.clock()
            row["capturePrepareMs"] = max(0, (ready - row["startedMonotonic"]) * 1000)
            row["startedMonotonic"] = ready
        return probe_id, scope, row

    def finish(self, handle, status, http_status=None):
        if handle is None:
            return
        probe_id, scope, row = handle
        if self._scope(probe_id) is not scope or row not in scope["records"]:
            return
        if row["status"] != "pending":
            return
        row.update(status=status, httpStatus=http_status,
                   elapsedMs=max(0, (self.clock() - row["startedMonotonic"]) * 1000))

    def public(self, probe_id, signing_key):
        scope = self._scope(probe_id, signing_key)
        if scope is None:
            return None
        # Return a copy, never the keyed scope or a mutable in-flight record.
        return {"schemaVersion": 1, "maxAttempts": scope["limit"],
                "limitReached": len(scope["records"]) >= scope["limit"],
                "attempts": json.loads(json.dumps(scope["records"]))}
