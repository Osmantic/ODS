"""Protected deployment client for the ordinary Google sign-in journey.

Maintainers supply the app registration once; individual owners must not need a
Google Cloud project. This module neither registers an app nor certifies its
brand/scopes with Google. Root custody proves who selected the local client,
not that Google has approved it. The default is disabled until a reviewed
release pins a registered client and its approved scopes.

The Desktop app uses the existing Source Broker PKCE flow. Desktop credentials
are public-client material and must not be described as a confidential server
secret. Per-owner tokens remain in the broker's separate private store.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener

from . import oauth

CONFIG_DIR = Path('/private/etc/ods' if sys.platform == 'darwin' else '/etc/ods')
PATH = CONFIG_DIR / 'portal-google-app.json'
RELEASE_PATH = CONFIG_DIR / 'portal-google-release-app.json'
RELEASE_ASSET = 'portal-google-desktop.json'
# The reviewed release commit sets this only after the official client and
# all requested scopes are approved. Never put the downloaded JSON here.
RELEASE_CLIENT = None
RELEASE_ORIGINS = frozenset({
    'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com',
})
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


def release_pin():
    pin = RELEASE_CLIENT
    if pin is None:
        return None
    fields = {'tag', 'sha256', 'projectId', 'clientId'}
    if (type(pin) is not dict or set(pin) != fields | {'scopes'}
            or any(type(pin[key]) is not str for key in fields)
            or not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', pin['tag'])
            or not re.fullmatch(r'[0-9a-f]{64}', pin['sha256'])
            or not oauth.PROJECT_ID.fullmatch(pin['projectId'])
            or not oauth.CLIENT_ID.fullmatch(pin['clientId'])):
        raise ManagedAppError('google-release-pin-invalid')
    from . import catalog

    capabilities = {capability['id']: True for capability in catalog.GOOGLE['capabilities']}
    scopes = sorted({*oauth.IDENTITY_SCOPES, *catalog.scopes_for('google', capabilities)})
    if pin['scopes'] != scopes:
        raise ManagedAppError('google-release-scopes-changed')
    return pin


def release_payload(client):
    """Canonical release bytes from Google's downloaded Desktop document."""
    value = {'kind': KIND, 'schemaVersion': 1, 'client': client}
    validate(value)
    raw = json.dumps(value, sort_keys=True, separators=(',', ':')).encode('utf-8')
    if len(raw) > MAX_BYTES:
        raise ManagedAppError('google-app-config-invalid')
    return raw


def validate_release(raw, pin):
    if len(raw) > MAX_BYTES or hashlib.sha256(raw).hexdigest() != pin['sha256']:
        raise ManagedAppError('google-release-client-mismatch')
    client = validate(_decode(raw))
    if any(client[key] != pin[key] for key in ('projectId', 'clientId')):
        raise ManagedAppError('google-release-client-mismatch')
    return client


def _read(path, owners):
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
        return raw
    except FileNotFoundError:
        if path.is_symlink():
            raise ManagedAppError('google-app-config-unsafe') from None
        raise ManagedAppError('google-app-not-configured') from None
    except OSError:
        raise ManagedAppError('google-app-config-unavailable') from None


def load(*, path=None, owners=None) -> dict[str, str]:
    """Read Root's local choice first, then the pinned release default."""
    selected_owners = OWNERS if owners is None else owners
    try:
        return validate(_decode(_read(PATH if path is None else Path(path), selected_owners)))
    except ManagedAppError as error:
        if path is not None or error.code != 'google-app-not-configured':
            raise
        pin = release_pin()
        if pin is None:
            raise
    return validate_release(_read(RELEASE_PATH, selected_owners), pin)


def _release_origin(url):
    parsed = urlsplit(url)
    return parsed.scheme == 'https' and parsed.netloc in RELEASE_ORIGINS


class _ReleaseRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _release_origin(newurl):
            raise ManagedAppError('google-release-download-unavailable')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download_release(pin):
    address = f"https://github.com/Osmantic/ODS/releases/download/{pin['tag']}/{RELEASE_ASSET}"
    try:
        with build_opener(_ReleaseRedirect()).open(address, timeout=12) as response:
            return response.read(MAX_BYTES + 1)
    except (URLError, OSError):
        raise ManagedAppError('google-release-download-unavailable') from None


def provision_release():
    """Install the checked release default without changing a local override."""
    if os.geteuid() != 0:
        raise ManagedAppError('google-release-root-required')
    pin = release_pin()
    if pin is None:
        return 'disabled'
    if os.path.lexists(PATH):
        return 'local-override'
    try:
        current = _read(RELEASE_PATH, OWNERS)
    except ManagedAppError as error:
        if error.code != 'google-app-not-configured':
            raise
    else:
        if hashlib.sha256(current).hexdigest() == pin['sha256']:
            validate_release(current, pin)
            return 'configured'
    raw = _download_release(pin)
    validate_release(raw, pin)
    _parents(RELEASE_PATH, OWNERS)
    fd, temporary = tempfile.mkstemp(prefix='.portal-google-release-', dir=RELEASE_PATH.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, RELEASE_PATH)
        directory = os.open(RELEASE_PATH.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return 'configured'


def provision_for_install():
    """A connection setup failure must not stop the rest of ODS installing."""
    try:
        provision_release()
    except (ManagedAppError, OSError) as error:
        code = error.code if isinstance(error, ManagedAppError) else 'google-release-write-unavailable'
        print(f'Warning: ODS could not provision Connect Google ({code}).', file=sys.stderr)


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
