"""Private preferences only; saving never claims to apply runtime settings.

Reuse the existing qualified store transactions, including custody, locks, CAS
and ambiguous-durability errors. The directory must already be provisioned by
the owner lifecycle. Null is durable reset intent, not an omitted preference.
"""
from __future__ import annotations

import sys

from pixel_provider.store import ProviderStore, StoreError
from pixel_provider.windows_store import WindowsProviderStore
from .contract import merge_preferences, validate_preferences

MAX_REVISION = 2**53 - 1


def default_document():
    return {"schemaVersion": 1, "revision": 0, "preferences": {}}


def normalize_document(value):
    if (type(value) is not dict or set(value) != {"schemaVersion", "revision", "preferences"}
            or type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1
            or type(value["revision"]) is not int or not 0 <= value["revision"] <= MAX_REVISION):
        raise StoreError("invalid-config")
    return {"schemaVersion": 1, "revision": value["revision"],
            "preferences": validate_preferences(value["preferences"])}


class _SettingsSaveMixin:
    def save_changes(self, changes, *, expected_revision):
        # Both checks precede any IO, including the lock-file open on load.
        if type(expected_revision) is not int or not 0 <= expected_revision < MAX_REVISION:
            raise StoreError("invalid-request")
        changes = validate_preferences(changes)
        current = normalize_document(self.load())
        if current["revision"] != expected_revision:
            raise StoreError("stale-revision")
        document = dict(current, preferences=merge_preferences(current["preferences"], changes))
        # Another writer may have committed after load; inherited save checks
        # the revision again while holding its exclusive transaction lock.
        return self.save(document, expected_revision=expected_revision)


class SettingsStore(_SettingsSaveMixin, ProviderStore):
    config_name = "pixel-settings.json"

    def __init__(self, directory):
        super().__init__(directory, validator=normalize_document, default_factory=default_document)


class WindowsSettingsStore(_SettingsSaveMixin, WindowsProviderStore):
    config_name = "pixel-settings.json"

    def __init__(self, directory):
        super().__init__(directory, validator=normalize_document, default_factory=default_document)


def create_settings_store(directory, *, platform=None):
    platform = sys.platform if platform is None else platform
    if platform == "win32":
        return WindowsSettingsStore(directory)
    if platform in ("linux", "darwin"):
        return SettingsStore(directory)
    raise StoreError("unsupported-platform")
