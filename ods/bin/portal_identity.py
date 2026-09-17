"""Host-owned Portal display-name persistence; runtime activation stays separate.

Reuses the qualified provider store transactions (POSIX/Windows custody,
locking, CAS, durability). Old strict runtime-settings validators ignore this
file; pixel-settings.json and provider-config.json remain untouched.
"""
from __future__ import annotations

import sys

import portal_identity_contract as contract
from pixel_provider.store import ProviderStore, StoreError
from pixel_provider.store_factory import (
    existing_directory,
    prepare_directory,
    provider_directory,
)
from pixel_provider.windows_store import WindowsProviderStore

CONFIG_NAME = "portal-identity.json"


def _validator(value):
    try:
        return contract.normalize_document(value)
    except ValueError:
        raise StoreError("invalid-config") from None


class PortalIdentityStore(ProviderStore):
    config_name = CONFIG_NAME

    def __init__(self, directory):
        super().__init__(directory, validator=_validator, default_factory=contract.default_document)


class WindowsPortalIdentityStore(WindowsProviderStore):
    config_name = CONFIG_NAME

    def __init__(self, directory):
        super().__init__(directory, validator=_validator, default_factory=contract.default_document)


def create_identity_store(directory, *, platform=None):
    platform = sys.platform if platform is None else platform
    if platform == "win32":
        return WindowsPortalIdentityStore(directory)
    if platform in ("linux", "darwin"):
        return PortalIdentityStore(directory)
    raise StoreError("unsupported-platform")


def get_identity(data_dir):
    directory = provider_directory(data_dir)
    with existing_directory(directory) as exists:
        return create_identity_store(directory).load() if exists else contract.default_document()


def save_identity(data_dir, body):
    try:
        edit = contract.normalize_edit(body)
    except ValueError:
        raise StoreError("invalid-request") from None
    document = {"schemaVersion": 1, "revision": edit["expectedRevision"],
                "displayName": edit["displayName"]}
    directory = provider_directory(data_dir)
    prepare_directory(directory)
    # Inherited save re-checks the revision under its exclusive lock and commits
    # the incremented document exactly once; return what it actually commits.
    return create_identity_store(directory).save(document, expected_revision=edit["expectedRevision"])
