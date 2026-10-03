"""Owner-private credentials and atomic public edits. No runtime activation.

References are immutable. Detaching/replacing a key does not revoke the remote
key; old files remain for recovery until explicit, reference-aware collection.
"""

import copy
import os
import re
import secrets

from .config import ConfigError, normalize_config, public_config
from .store import ProviderStore, StoreError, _private

MAX_KEY_BYTES = 8192
_REF = re.compile(r"key-[a-f0-9]{32}")


def _key(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= MAX_KEY_BYTES
            or any(not 33 <= ord(char) <= 126 for char in value)):
        raise StoreError("invalid-request")
    return value


def validate_edit(body):
    """Validate public input without treating its presence hint as authority."""
    if (not isinstance(body, dict)
            or not {"document", "expectedRevision"} <= set(body) <= {"document", "expectedRevision", "credentialChanges"}
            or type(body["expectedRevision"]) is not int
            or not 0 <= body["expectedRevision"] < 2**53 - 1
            or not isinstance(body["document"], dict)):
        raise StoreError("invalid-request")
    body = copy.deepcopy(body)
    document = body["document"]
    providers = document.get("providers")
    changes = body.get("credentialChanges", {})
    if (not isinstance(providers, list) or len(providers) > 32
            or not isinstance(changes, dict) or len(changes) > 32):
        raise StoreError("invalid-request")
    ids = set()
    for provider in providers:
        if (not isinstance(provider, dict) or "credentialRef" in provider
                or type(provider.get("hasCredential")) is not bool
                or not isinstance(provider.get("id"), str)):
            raise StoreError("invalid-request")
        ids.add(provider["id"])
    for pid, action in changes.items():
        if pid not in ids or not isinstance(action, dict):
            raise StoreError("invalid-request")
        if action.get("action") == "set" and set(action) == {"action", "value"}:
            _key(action["value"])
        elif action.get("action") != "remove" or set(action) != {"action"}:
            raise StoreError("invalid-request")
    candidate = copy.deepcopy(document)
    for provider in candidate["providers"]:
        provider.pop("hasCredential")
        action = changes.get(provider["id"], {}).get("action")
        # Shape-only preflight; the actual old reference is resolved under the
        # lock. Even a false presence hint must not discard a stored cloud key.
        provider["credentialRef"] = "pending-key" if action != "remove" else None
    try:
        normalize_config(candidate)
    except (ConfigError, TypeError, RecursionError):
        raise StoreError("invalid-config") from None
    if document["revision"] != body["expectedRevision"]:
        raise StoreError("stale-revision")
    return body


class CredentialStore(ProviderStore):
    def save_public(self, body):
        body = validate_edit(body)
        expected = body["expectedRevision"]
        changes = body.get("credentialChanges", {})
        with self._locked(True) as directory_fd:
            current = self._load(directory_fd)
            if current["revision"] != expected:
                raise StoreError("stale-revision")
            previous = {provider["id"]: provider for provider in current["providers"]}
            candidate = copy.deepcopy(body["document"])
            pending = {}
            for provider in candidate["providers"]:
                provider.pop("hasCredential")
                old = previous.get(provider["id"], {})
                provider["credentialRef"] = old.get("credentialRef")
                action = changes.get(provider["id"], {})
                if action.get("action") == "set":
                    ref = "key-" + secrets.token_hex(16)
                    if "." + ref + ".key" in pending:
                        raise StoreError("credential-write-failed")
                    provider["credentialRef"] = ref
                    pending["." + ref + ".key"] = action["value"].encode("ascii")
                elif action.get("action") == "remove":
                    provider["credentialRef"] = None
            candidate = self._validate(candidate)
            for provider in candidate["providers"]:
                old = previous.get(provider["id"], {})
                if (old.get("credentialRef") and provider["id"] not in changes
                        and (provider["baseUrl"] != old["baseUrl"] or provider["kind"] != old["kind"])):
                    raise StoreError("credential-target-changed")
            created = []
            try:
                for name, raw in pending.items():
                    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=directory_fd)
                    created.append(name)
                    try:
                        if not _private(os.fstat(fd)):
                            raise StoreError("unsafe-file")
                        remaining = memoryview(raw)
                        while remaining:
                            count = os.write(fd, remaining)
                            if count <= 0:
                                raise OSError("short-write")
                            remaining = remaining[count:]
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                # Secret directory entries must be durable before a config can
                # durably refer to them. Never overwrite or remove an old key.
                if created:
                    os.fsync(directory_fd)
                saved = self._commit(directory_fd, candidate, expected)
            except (StoreError, OSError) as exc:
                ambiguous = isinstance(exc, StoreError) and exc.code == "write-durability-unknown"
                if not ambiguous:
                    for name in created:
                        try:
                            os.unlink(name, dir_fd=directory_fd)
                        except OSError:
                            pass  # Preserve the original error, retain recoverable orphans.
                if isinstance(exc, StoreError):
                    raise
                raise StoreError("credential-write-failed") from None
            return public_config(saved)

    def resolve_credential(self, provider_id, *, expected_revision):
        """Host runtime only. Never expose this function through the dashboard."""
        if type(expected_revision) is not int or not isinstance(provider_id, str):
            raise StoreError("invalid-request")
        with self._locked(False) as directory_fd:
            current = self._load(directory_fd)
            if current["revision"] != expected_revision:
                raise StoreError("stale-revision")
            provider = next((item for item in current["providers"] if item["id"] == provider_id), None)
            if provider is None:
                raise StoreError("invalid-request")
            ref = provider["credentialRef"]
            if ref is None:
                return None
            if not _REF.fullmatch(ref):
                raise StoreError("credential-unavailable")
            try:
                fd = self._open_file(directory_fd, "." + ref + ".key")
                try:
                    raw = bytearray()
                    while len(raw) <= MAX_KEY_BYTES:
                        chunk = os.read(fd, MAX_KEY_BYTES + 1 - len(raw))
                        if not chunk:
                            break
                        raw.extend(chunk)
                    return _key(bytes(raw).decode("ascii"))
                finally:
                    os.close(fd)
            except (OSError, ValueError):
                raise StoreError("credential-unavailable") from None
