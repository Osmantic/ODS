"""Bounded completion-usage observations; never retain prompts or response text."""
import hashlib
import json
import time


def route_fingerprint(state):
    provider = state.get('provider') or {}
    identity = {key: provider.get(key) for key in (
        'transport', 'baseUrl', 'model', 'contextLength', 'maxTokens', 'reasoning')}
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class CompletionObservation:
    LIMIT = 64 * 1024

    def __init__(self, route):
        self.fingerprint = route.get('routeFingerprint')
        self.model = (route.get('provider') or {}).get('model')
        self.started = time.monotonic()
        self.buffer = b''
        self.tokens = None
        self.complete = False
        self.invalid = False

    def payload(self, value):
        if not isinstance(value, dict):
            return
        if value.get('error') is not None or value.get('type') in ('error', 'response.failed', 'response.incomplete'):
            self.invalid = True
            return
        if value.get('type') == 'response.completed':
            self.complete = True
            value = value.get('response', {})
        usage = value.get('usage') if isinstance(value, dict) else None
        if isinstance(usage, dict):
            count = usage.get('completion_tokens', usage.get('output_tokens'))
            if type(count) is int and 0 < count <= 100_000_000:
                self.tokens = count

    def feed(self, chunk):
        if self.invalid:
            return
        # Process arbitrary network chunk boundaries without accumulating a response.
        for piece in chunk.splitlines(keepends=True):
            if len(self.buffer) + len(piece) > self.LIMIT:
                self.buffer = b''
                self.invalid = True
                return
            self.buffer += piece
            if not self.buffer.endswith(b'\n'):
                continue
            line, self.buffer = self.buffer.strip(), b''
            if not line.startswith(b'data:'):
                continue
            data = line[5:].strip()
            if data == b'[DONE]':
                self.complete = True
                continue
            try:
                self.payload(json.loads(data))
            except (ValueError, UnicodeError, RecursionError):
                self.invalid = True

    def result(self):
        duration = time.monotonic() - self.started
        if (self.invalid or not self.complete or not self.tokens or not self.fingerprint
                or not self.model or not 0 < duration <= 3600):
            return None
        return {'schemaVersion': 1, 'routeFingerprint': self.fingerprint, 'model': self.model,
                'completionTokens': self.tokens, 'elapsedMs': duration * 1000,
                'sampledAt': time.time() * 1000}
