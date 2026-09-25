"""Bounded response measurements for explicitly armed probes, never raw traces."""
import json
import math
import re


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 10**15 else None


def response_metrics(payload):
    if type(payload) is not dict:
        return {}
    result = {}
    usage = payload.get("usage")
    if type(usage) is dict:
        result["usage"] = {key: number(usage.get(key)) for key in
                           ("prompt_tokens", "completion_tokens", "total_tokens")}
        for parent, field in (("prompt_tokens_details", "cached_tokens"),
                              ("completion_tokens_details", "reasoning_tokens")):
            child = usage.get(parent)
            result["usage"][parent] = {field: number(child.get(field)) if type(child) is dict else None}
    timings = payload.get("timings")
    if type(timings) is dict:
        result["timings"] = {key: number(timings.get(key)) for key in
            ("prompt_n", "prompt_ms", "predicted_n", "predicted_ms", "cache_n", "draft_n", "draft_n_accepted")}
    identity = payload.get("id")
    if isinstance(identity, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", identity):
        result["responseId"] = identity
    return result


class ProbeStream:
    """Ephemeral bounded SSE parsing; output contains numeric metrics only."""
    MAX_PENDING = 65536
    MAX_BYTES = 2 * 1024 * 1024

    def __init__(self, observe):
        self.observe = observe
        self.pending = b""
        self.total = 0
        self.available = True

    def feed(self, chunk):
        if not self.available:
            return
        self.total += len(chunk)
        if self.total > self.MAX_BYTES:
            self.pending = b""
            self.available = False
            return
        self.pending += chunk
        while b"\n" in self.pending:
            line, self.pending = self.pending.split(b"\n", 1)
            if len(line) > self.MAX_PENDING:
                self.available = False
                self.pending = b""
                return
            if line.startswith(b"data:"):
                try:
                    self.observe(json.loads(line[5:].strip()))
                except (ValueError, UnicodeError):
                    pass
        if len(self.pending) > self.MAX_PENDING:
            self.available = False
            self.pending = b""
