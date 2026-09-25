"""Inference-only per-device grants. No agent, model-management or tool authority."""

import copy
import hashlib
import re
import secrets
import time

from .store import ProviderStore, StoreError

PUBLIC_MODEL = 'ods/shared'
_TOKEN = re.compile(r'ods_infer_[a-f0-9]{64}')


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _text(value, maximum=256):
    return (isinstance(value, str) and 1 <= len(value) <= maximum and value == value.strip()
            and all(32 <= ord(char) < 127 for char in value))


def default_sharing():
    return {'schemaVersion': 1, 'revision': 0, 'enabled': False, 'devices': []}


def normalize_sharing(document):
    if (not isinstance(document, dict) or set(document) != {'schemaVersion', 'revision', 'enabled', 'devices'}
            or type(document['schemaVersion']) is not int or document['schemaVersion'] != 1
            or not _integer(document['revision'], 0, 2**53 - 1) or type(document['enabled']) is not bool
            or not isinstance(document['devices'], list) or len(document['devices']) > 64):
        raise StoreError('invalid-config')
    seen = set()
    hashes = set()
    for item in document['devices']:
        if (not isinstance(item, dict) or set(item) != {'id', 'label', 'tokenHash', 'catalogId',
                'runtimeModelId', 'createdAt', 'expiresAt', 'revoked', 'maxConcurrent',
                'maxOutputTokens', 'deadlineSeconds', 'requestsPerMinute'}
                or not isinstance(item['id'], str) or not re.fullmatch(r'device-[a-f0-9]{16}', item['id'])
                or not isinstance(item['tokenHash'], str) or not re.fullmatch(r'[a-f0-9]{64}', item['tokenHash'])
                or any(not _text(item[key]) for key in ('label', 'catalogId', 'runtimeModelId'))
                or not _integer(item['createdAt'], 0, 2**53 - 1)
                or not _integer(item['expiresAt'], item['createdAt'] + 1, 2**53 - 1)
                or type(item['revoked']) is not bool
                or not _integer(item['maxConcurrent'], 1, 8)
                or not _integer(item['maxOutputTokens'], 1, 131072)
                or not _integer(item['deadlineSeconds'], 1, 3600)
                or not _integer(item['requestsPerMinute'], 1, 600)
                or item['id'] in seen or item['tokenHash'] in hashes):
            raise StoreError('invalid-config')
        seen.add(item['id'])
        hashes.add(item['tokenHash'])
    return copy.deepcopy(document)


def public_sharing(document):
    result = normalize_sharing(document)
    for device in result['devices']:
        del device['tokenHash']
    return result


def validate_settings(settings):
    if (not isinstance(settings, dict) or set(settings) != {'label', 'catalogId', 'runtimeModelId',
            'ttlSeconds', 'maxConcurrent', 'maxOutputTokens', 'deadlineSeconds', 'requestsPerMinute'}
            or not _integer(settings['ttlSeconds'], 60, 365 * 86400)):
        raise StoreError('invalid-request')
    # Reuse the on-disk schema before creating any filesystem state.
    device = {name: settings[name] for name in settings if name != 'ttlSeconds'}
    device.update(id='device-' + '0' * 16, tokenHash='0' * 64, createdAt=0,
                  expiresAt=settings['ttlSeconds'], revoked=False)
    normalize_sharing({**default_sharing(), 'devices': [device]})
    return copy.deepcopy(settings)


class SharingStore(ProviderStore):
    config_name = 'inference-sharing.json'

    def __init__(self, directory):
        super().__init__(directory, validator=normalize_sharing, default_factory=default_sharing)

    def _change(self, expected_revision, update):
        if not _integer(expected_revision, 0, 2**53 - 2):
            raise StoreError('invalid-request')
        with self._locked(True) as directory_fd:
            current = self._load(directory_fd)
            if current['revision'] != expected_revision:
                raise StoreError('stale-revision')
            update(current)
            return self._commit(directory_fd, current, expected_revision)

    def issue(self, settings, *, expected_revision, now=None):
        settings = validate_settings(settings)
        stamp = int(time.time()) if now is None else now
        if not _integer(stamp, 0, 2**53 - 365 * 86400 - 1):
            raise StoreError('invalid-request')
        key = 'ods_infer_' + secrets.token_hex(32)
        device = {name: settings[name] for name in settings if name != 'ttlSeconds'}
        device.update(id='device-' + secrets.token_hex(8), tokenHash=hashlib.sha256(key.encode()).hexdigest(),
                      createdAt=stamp, expiresAt=stamp + settings['ttlSeconds'], revoked=False)
        saved = self._change(expected_revision, lambda doc: doc['devices'].append(device))
        return {'configuration': public_sharing(saved), 'credential': {'id': device['id'], 'key': key},
                'model': PUBLIC_MODEL}

    def set_enabled(self, enabled, *, expected_revision):
        if type(enabled) is not bool:
            raise StoreError('invalid-request')
        return public_sharing(self._change(expected_revision, lambda doc: doc.update(enabled=enabled)))

    def disable_after_failed_start(self):
        """Close failed activation while preserving concurrent grant revocations.

        The host lifecycle lock excludes another start/enable until this ends.
        Grant changes may advance revision during a slow Docker build; those
        changes must survive without keeping a failed activation's admission on.
        """
        with self._locked(True) as directory_fd:
            current = self._load(directory_fd)
            if not current['enabled']:
                return public_sharing(current)
            revision = current['revision']
            current['enabled'] = False
            return public_sharing(self._commit(directory_fd, current, revision))

    def revoke(self, device_id, *, expected_revision):
        def update(doc):
            device = next((item for item in doc['devices'] if item['id'] == device_id), None)
            if device is None:
                raise StoreError('invalid-request')
            device['revoked'] = True
        return public_sharing(self._change(expected_revision, update))

    def authenticate(self, token, *, now=None):
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise StoreError('invalid-credential')
        document = self.read_snapshot()
        digest = hashlib.sha256(token.encode('ascii')).hexdigest()
        stamp = time.time() if now is None else now
        match = next((item for item in document['devices'] if secrets.compare_digest(item['tokenHash'], digest)), None)
        if (not document['enabled'] or match is None or match['revoked']
                or not match['createdAt'] <= stamp < match['expiresAt']):
            raise StoreError('invalid-credential')
        result = copy.deepcopy(match)
        del result['tokenHash']
        return result
