"""Protected deployment client for the ordinary Google sign-in journey.

Maintainers supply the app registration once; individual owners must not need a
Google Cloud project. This module neither registers an app nor certifies its
brand/scopes with Google. Root custody proves who selected the local client,
not that Google has approved it. No client is shipped or guessed here.

The Desktop app uses the existing Source Broker PKCE flow. Desktop credentials
are public-client material and must not be described as a confidential server
secret. Per-owner tokens remain in the broker's separate private store.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
from typing import Any

from . import oauth

PATH = Path('/etc/ods/portal-google-app.json')
OWNERS = (0,)
MAX_BYTES = 16 * 1024
KIND = 'ods-managed-google-app'
PUBLIC_KEYS = {'mode', 'state', 'advancedAvailable'}
PUBLIC_STATES = ('configured', 'not-configured', 'unavailable')


class ManagedAppError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def validate(value: Any) -> dict[str, str]:
    """Validate the deployment envelope; no input here asserts Google approval."""
    if (type(value) is not dict or set(value) != {'kind', 'schemaVersion', 'client'}
            or value['kind'] != KIND or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1):
        raise ManagedAppError('google-app-config-invalid')
    try:
        return oauth.parse_client(value['client'])
    except oauth.OAuthError:
        raise ManagedAppError('google-app-config-invalid') from None


def _decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result

    def constant(_value):
        raise ValueError()

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ManagedAppError('google-app-config-invalid') from None


def _parents(path, owners):
    if not path.is_absolute():
        raise ManagedAppError('google-app-config-unsafe')
    for parent in path.parents:
        info = parent.lstat()
        # Permit the system's sticky /tmp only for isolated fixtures; no other
        # account may replace an ancestor of the fixed production location.
        sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, *owners)
                or info.st_mode & 0o022 and not sticky_root):
            raise ManagedAppError('google-app-config-unsafe')


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def load(*, path=None, owners=None) -> dict[str, str]:
    """Root reads its fixed configuration, never a browser-selected path/client.

    Optional arguments support trusted deployment tooling and disposable tests;
    the public request contract must never accept either of them. Missing and
    unusable configuration have fixed distinct errors without reflecting bytes.
    """
    path = PATH if path is None else Path(path)
    owners = OWNERS if owners is None else owners
    try:
        _parents(path, owners)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(fd, 'rb') as handle:
            before = os.fstat(handle.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_uid not in owners
                    or before.st_mode & 0o077 or before.st_size > MAX_BYTES):
                raise ManagedAppError('google-app-config-unsafe')
            raw = handle.read(MAX_BYTES + 1)
            if (len(raw) > MAX_BYTES or _identity(before) != _identity(os.fstat(handle.fileno()))
                    or _identity(before) != _identity(path.lstat())):
                raise ManagedAppError('google-app-config-unavailable')
        return validate(_decode(raw))
    except FileNotFoundError:
        if path.is_symlink():
            raise ManagedAppError('google-app-config-unsafe') from None
        raise ManagedAppError('google-app-not-configured') from None
    except OSError:
        raise ManagedAppError('google-app-config-unavailable') from None


def public_status(*, path=None, owners=None) -> dict[str, Any]:
    """Credential-free configuration status; 'configured' never means verified."""
    try:
        load(path=path, owners=owners)
        state = 'configured'
    except ManagedAppError as error:
        state = 'not-configured' if error.code == 'google-app-not-configured' else 'unavailable'
    return {'mode': 'managed', 'state': state, 'advancedAvailable': True}


def validate_public(value: Any) -> dict[str, Any]:
    if (type(value) is not dict or set(value) != PUBLIC_KEYS or value['mode'] != 'managed'
            or value['state'] not in PUBLIC_STATES or type(value['advancedAvailable']) is not bool):
        raise ManagedAppError('google-app-status-invalid')
    return dict(value)
