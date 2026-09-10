"""Owner control plane. No service activation or changes to the active model."""

import os
import re
from pathlib import Path

from .sharing import SharingStore, default_sharing, public_sharing, validate_settings
from .store import StoreError


def _directory(data_dir):
    if os.name != 'posix':
        raise StoreError('unsupported-platform')
    return Path(data_dir) / 'pixel-inference'


def _envelope(configuration, route):
    return {'configuration': configuration, 'activeRoute': route,
            'transport': {'mode': 'loopback-only', 'defaultPort': 4005, 'port': 4005},
            'runtime': {'status': 'not-probed'}}


def get_sharing(data_dir, route):
    directory = _directory(data_dir)
    try:
        directory.lstat()
    except FileNotFoundError:
        return _envelope(public_sharing(default_sharing()), route)
    return _envelope(public_sharing(SharingStore(directory).load()), route)


def change_sharing(data_dir, action, body, route):
    field = {'issue': 'settings', 'enable': 'enabled', 'revoke': 'deviceId'}.get(action)
    if (field is None or not isinstance(body, dict) or set(body) != {'expectedRevision', field}
            or type(body['expectedRevision']) is not int or not 0 <= body['expectedRevision'] < 2**53 - 1):
        raise StoreError('invalid-request')
    if action == 'issue':
        settings = validate_settings(body['settings'])
        if (route is None or any(settings[key] != route.get(key) for key in ('catalogId', 'runtimeModelId'))):
            raise StoreError('active-route-changed')
    elif action == 'enable':
        if type(body['enabled']) is not bool:
            raise StoreError('invalid-request')
        if body['enabled'] and route is None:
            raise StoreError('active-route-changed')
    elif not isinstance(body['deviceId'], str) or not re.fullmatch(r'device-[a-f0-9]{16}', body['deviceId']):
        raise StoreError('invalid-request')
    directory = _directory(data_dir)
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        pass
    store = SharingStore(directory)
    if action == 'issue':
        issued = store.issue(settings, expected_revision=body['expectedRevision'])
        return {**_envelope(issued['configuration'], route), 'credential': issued['credential'], 'model': issued['model']}
    if action == 'enable':
        saved = store.set_enabled(body['enabled'], expected_revision=body['expectedRevision'])
    else:
        saved = store.revoke(body['deviceId'], expected_revision=body['expectedRevision'])
    return _envelope(saved, route)
