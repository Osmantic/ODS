"""Explicit native Windows credentials; no automatic platform activation.

Uses the existing public-edit/key validators with actual NTFS transactions.
New immutable keys are published before their configuration references. Old
keys and privately published orphans are retained for future reference-aware
recovery/collection; removing a reference does not revoke a remote credential.
This adapter does not qualify reboot/power-loss recovery or create its root.
"""
import copy
import secrets

from .config import public_config
from .store import StoreError
from .vault import MAX_KEY_BYTES, _REF, _key, validate_edit
from .windows_custody import WindowsCustodyError
from .windows_store import WindowsProviderStore


class WindowsCredentialStore(WindowsProviderStore):
    def save_public(self, body):
        body = validate_edit(body)
        expected = body['expectedRevision']
        changes = body.get('credentialChanges', {})
        with self._locked(True) as transaction:
            current = self._load(transaction)
            if current['revision'] != expected:
                raise StoreError('stale-revision')
            previous = {provider['id']: provider for provider in current['providers']}
            candidate = copy.deepcopy(body['document'])
            pending = {}
            for provider in candidate['providers']:
                provider.pop('hasCredential')
                old = previous.get(provider['id'], {})
                provider['credentialRef'] = old.get('credentialRef')
                action = changes.get(provider['id'], {})
                if action.get('action') == 'set':
                    ref = 'key-' + secrets.token_hex(16)
                    name = '.' + ref + '.key'
                    if name in pending:
                        raise StoreError('credential-write-failed')
                    provider['credentialRef'] = ref
                    pending[name] = action['value'].encode('ascii')
                elif action.get('action') == 'remove':
                    provider['credentialRef'] = None
            candidate = self._validate(candidate)
            for provider in candidate['providers']:
                old = previous.get(provider['id'], {})
                if (old.get('credentialRef') and provider['id'] not in changes
                        and (provider['baseUrl'] != old['baseUrl'] or provider['kind'] != old['kind'])):
                    raise StoreError('credential-target-changed')
            # There is deliberately no failure cleanup by filename. A key may
            # have been published despite an error, or the config may refer to
            # it. Keep all published keys until explicit recovery can prove
            # which references are live. Never overwrite an existing key.
            for name, raw in pending.items():
                try:
                    transaction.create_immutable(name, raw)
                except WindowsCustodyError as error:
                    code = ('write-durability-unknown' if error.code == 'write-durability-unknown'
                            else 'credential-write-failed')
                    raise StoreError(code) from None
            return public_config(self._commit(transaction, candidate, expected))

    def resolve_credential(self, provider_id, *, expected_revision):
        """Host-only resolution; never expose secrets through a public API."""
        if type(expected_revision) is not int or not isinstance(provider_id, str):
            raise StoreError('invalid-request')
        with self._locked(False) as transaction:
            current = self._load(transaction)
            if current['revision'] != expected_revision:
                raise StoreError('stale-revision')
            provider = next((item for item in current['providers'] if item['id'] == provider_id), None)
            if provider is None:
                raise StoreError('invalid-request')
            ref = provider['credentialRef']
            if ref is None:
                return None
            if not _REF.fullmatch(ref):
                raise StoreError('credential-unavailable')
            try:
                raw = transaction.read('.' + ref + '.key', max_bytes=MAX_KEY_BYTES)
                if raw is None:
                    raise StoreError('credential-unavailable')
                return _key(raw.decode('ascii'))
            except (WindowsCustodyError, ValueError):
                raise StoreError('credential-unavailable') from None
