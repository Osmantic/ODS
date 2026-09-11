"""Explicit native Windows configuration store; no automatic platform switch."""
import copy
from contextlib import contextmanager
import json
import os

from .store import LOCK_TIMEOUT, MAX_BYTES, ProviderStore, StoreError, decode_document
from .windows_custody import WindowsCustodyError
from .windows_transactions import private_directory_transaction


class WindowsProviderStore(ProviderStore):
    def __init__(self, directory, *, validator=None, default_factory=None):
        from .config import default_config, normalize_config
        # Path(...).absolute() erases '.' and accepts relative spellings before
        # the native custody validator can reject them. Snapshot raw fspath.
        self.directory = os.fspath(directory)
        self.validator = validator or normalize_config
        self.default_factory = default_factory or default_config

    @contextmanager
    def _locked(self, exclusive):
        try:
            with private_directory_transaction(self.directory, exclusive=exclusive,
                                               timeout=LOCK_TIMEOUT) as transaction:
                yield transaction
        except WindowsCustodyError as error:
            retained = {'unsupported-platform', 'lock-timeout', 'write-durability-unknown',
                        'write-failed', 'invalid-request'}
            raise StoreError(error.code if error.code in retained else 'storage-unavailable') from None

    def _load(self, transaction):
        try:
            raw = transaction.read(self.config_name, max_bytes=MAX_BYTES)
        except WindowsCustodyError as error:
            if error.code == 'file-too-large':
                raise StoreError('malformed-json') from None
            raise
        if raw is None:
            result = self._validate(self.default_factory())
            if result['revision'] != 0:
                raise StoreError('invalid-config')
            return result
        return self._validate(decode_document(raw))

    def read_snapshot(self):
        # Native snapshots participate in the stable shared lock, creating it
        # if missing. This is not the POSIX read-only-mount snapshot contract.
        return self.load()

    def _commit(self, transaction, candidate, expected_revision):
        candidate = self._validate(candidate)
        if candidate['revision'] != expected_revision:
            raise StoreError('stale-revision')
        candidate['revision'] += 1
        candidate = self._validate(candidate)
        if candidate['revision'] != expected_revision + 1:
            raise StoreError('invalid-config')
        try:
            raw = json.dumps(candidate, ensure_ascii=True, allow_nan=False,
                             separators=(',', ':')).encode('utf-8') + b'\n'
        except (ValueError, TypeError, RecursionError):
            raise StoreError('invalid-config') from None
        if len(raw) > MAX_BYTES:
            raise StoreError('invalid-config')
        transaction.replace(self.config_name, raw)
        return copy.deepcopy(candidate)
