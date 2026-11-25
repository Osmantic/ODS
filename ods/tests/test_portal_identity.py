"""Contract tests for bin/portal_identity.py.

`get_identity`/`save_identity` are the host-agent's persistence boundary for
the Portal display name (`ods-host-agent.py` `_handle_portal_identity`). The
document lives in the qualified provider store (`pixel-providers/
portal-identity.json`) so revision CAS, locking and private-file custody are
shared with provider Settings; this file pins the portal-specific layer on top.
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import portal_identity
import portal_identity_contract as contract
from pixel_provider.store import StoreError

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX store custody')


class TestContract:
    def test_default_document(self):
        assert contract.default_document() == {'schemaVersion': 1, 'revision': 0,
                                               'displayName': 'Portal'}

    def test_normalize_name_strips_and_defaults(self):
        assert contract.normalize_name('  Ada  ') == 'Ada'
        assert contract.normalize_name('   ') == 'Portal'
        assert contract.normalize_name(' ' * 240) == 'Portal'

    def test_normalize_name_composes_nfc(self):
        assert contract.normalize_name('Café') == 'Café'

    def test_joiners_allowed_other_format_chars_rejected(self):
        assert contract.normalize_name('a‌b‍c') == 'a‌b‍c'
        for bad in ('a\x0bb', 'a‎b', 'a⁦b', 'a﻿b'):
            with pytest.raises(ValueError, match='invalid-name-character'):
                contract.normalize_name(bad)

    def test_surrogates_and_separators_rejected(self):
        for bad in ('bad\ud800name', 'a\u2028b', 'a\u2029b'):
            with pytest.raises(ValueError, match='invalid-name-character'):
                contract.normalize_name(bad)

    def test_name_length_bounds(self):
        assert contract.normalize_name('x' * contract.MAX_NAME_CODEPOINTS) == 'x' * 60
        with pytest.raises(ValueError, match='name-too-long'):
            contract.normalize_name('x' * (contract.MAX_NAME_CODEPOINTS + 1))
        with pytest.raises(ValueError, match='name-too-long'):
            contract.normalize_name('x' * (contract.MAX_RAW_NAME_CODEPOINTS + 1))
        # A padded name that strips inside the bound stays legal.
        assert contract.normalize_name(' ' + 'y' * 60 + ' ') == 'y' * 60

    def test_normalize_name_rejects_non_strings(self):
        for bad in (None, 7, ['Portal'], b'Portal'):
            with pytest.raises(ValueError, match='invalid-name'):
                contract.normalize_name(bad)

    def test_document_requires_exact_keys_and_canonical_name(self):
        document = contract.normalize_document(
            {'schemaVersion': 1, 'revision': 4, 'displayName': 'Ada'})
        assert document == {'schemaVersion': 1, 'revision': 4, 'displayName': 'Ada'}
        with pytest.raises(ValueError, match='non-canonical-display-name'):
            contract.normalize_document(
                {'schemaVersion': 1, 'revision': 0, 'displayName': ' Ada '})
        for bad in ({'schemaVersion': 2, 'revision': 0, 'displayName': 'Portal'},
                    {'schemaVersion': 1, 'revision': -1, 'displayName': 'Portal'},
                    {'schemaVersion': 1, 'revision': contract.MAX_REVISION + 1,
                     'displayName': 'Portal'},
                    {'schemaVersion': 1, 'revision': 0, 'displayName': 'Portal', 'x': 1},
                    {'schemaVersion': 1, 'revision': 0}):
            with pytest.raises(ValueError, match='invalid-document'):
                contract.normalize_document(bad)

    def test_edit_bounds(self):
        edit = contract.normalize_edit({'expectedRevision': 0, 'displayName': ' New '})
        assert edit == {'expectedRevision': 0, 'displayName': 'New'}
        with pytest.raises(ValueError, match='invalid-edit'):
            contract.normalize_edit({'expectedRevision': contract.MAX_REVISION,
                                     'displayName': 'x'})


class TestStore:
    def test_platform_dispatch(self, tmp_path, monkeypatch):
        sentinel = object()
        monkeypatch.setattr(portal_identity, 'WindowsPortalIdentityStore',
                            lambda directory: sentinel)
        assert portal_identity.create_identity_store(tmp_path, platform='win32') is sentinel
        for platform in ('linux', 'darwin'):
            store = portal_identity.create_identity_store(tmp_path, platform=platform)
            assert type(store) is portal_identity.PortalIdentityStore
            assert store.config_name == 'portal-identity.json'
        with pytest.raises(StoreError, match='unsupported-platform'):
            portal_identity.create_identity_store(tmp_path, platform='sunos')

    def test_get_identity_default_leaves_no_state(self, tmp_path):
        assert portal_identity.get_identity(tmp_path) == contract.default_document()
        assert not (tmp_path / 'pixel-providers').exists()

    def test_save_and_get_roundtrip(self, tmp_path):
        saved = portal_identity.save_identity(
            tmp_path, {'expectedRevision': 0, 'displayName': '  My Portal  '})
        assert saved == {'schemaVersion': 1, 'revision': 1, 'displayName': 'My Portal'}
        assert portal_identity.get_identity(tmp_path) == saved
        target = tmp_path / 'pixel-providers' / 'portal-identity.json'
        assert json.loads(target.read_text()) == saved
        assert os.stat(tmp_path / 'pixel-providers').st_mode & 0o777 == 0o700
        assert os.stat(target).st_mode & 0o777 == 0o600

    def test_revision_cas(self, tmp_path):
        portal_identity.save_identity(tmp_path, {'expectedRevision': 0, 'displayName': 'A'})
        with pytest.raises(StoreError, match='stale-revision'):
            portal_identity.save_identity(tmp_path, {'expectedRevision': 0, 'displayName': 'B'})
        saved = portal_identity.save_identity(
            tmp_path, {'expectedRevision': 1, 'displayName': 'B'})
        assert saved['revision'] == 2 and saved['displayName'] == 'B'

    @pytest.mark.parametrize('body', [
        {}, {'expectedRevision': 0}, {'displayName': 'x'},
        {'expectedRevision': '0', 'displayName': 'x'},
        {'expectedRevision': -1, 'displayName': 'x'},
        {'expectedRevision': 0, 'displayName': 5},
        {'expectedRevision': 0, 'displayName': 'x', 'extra': 1},
        {'expectedRevision': 0, 'displayName': 'a\rb'},
    ])
    def test_invalid_edits_are_invalid_request(self, tmp_path, body):
        with pytest.raises(StoreError, match='invalid-request'):
            portal_identity.save_identity(tmp_path, body)
        assert portal_identity.get_identity(tmp_path) == contract.default_document()

    def test_corrupt_stored_document_is_invalid_config(self, tmp_path):
        directory = tmp_path / 'pixel-providers'
        directory.mkdir(mode=0o700)
        target = directory / 'portal-identity.json'
        target.write_text(json.dumps({'schemaVersion': 1, 'revision': 0,
                                      'displayName': 'not canonical '}))
        os.chmod(target, 0o600)
        with pytest.raises(StoreError, match='invalid-config'):
            portal_identity.get_identity(tmp_path)
