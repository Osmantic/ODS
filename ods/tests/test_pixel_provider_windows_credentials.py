"""Native NTFS credential journeys; synthetic secrets, retained private state."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import test_pixel_provider_windows_custody as fixtures
from pixel_provider import windows_custody as W
from pixel_provider import windows_transactions as T
from pixel_provider.config import default_config
from pixel_provider.store import StoreError
from pixel_provider.windows_vault import WindowsCredentialStore


def document():
    result = default_config()
    result['providers'] = [dict(id='tower', label='Tower', kind='ods-peer',
                               baseUrl='https://tower.example/v1', model='model',
                               contextTokens=32768, maxOutputTokens=4096, supportsTools=True,
                               supportsVision=False, reasoning=False, hasCredential=False, enabled=True)]
    return result


@unittest.skipUnless(os.name == 'nt', 'Requires actual native Windows APIs')
class NativeCredentials(unittest.TestCase):
    setUpClass = classmethod(fixtures.NativeCustody.setUpClass.__func__)
    descriptor = fixtures.NativeCustody.descriptor
    create = fixtures.NativeCustody.create
    change_acl = fixtures.NativeCustody.change_acl

    def setUp(self):
        fixtures.NativeCustody.setUp(self)
        self.store = WindowsCredentialStore(self.directory)

    def save(self, doc, changes=None):
        body = dict(document=doc, expectedRevision=doc['revision'])
        if changes is not None:
            body['credentialChanges'] = changes
        return self.store.save_public(body)

    def seed(self):
        return self.save(document(), {'tower': {'action': 'set', 'value': 'synthetic-key'}})

    def key_path(self):
        ref = self.store.load()['providers'][0]['credentialRef']
        return self.directory / ('.' + ref + '.key')

    def assertCode(self, expected, call):
        with self.assertRaises(StoreError) as context:
            call()
        self.assertEqual(context.exception.code, expected)
        return context.exception

    def test_public_roundtrip_private_key_and_input_unchanged(self):
        doc = document()
        before = copy.deepcopy(doc)
        saved = self.save(doc, {'tower': {'action': 'set', 'value': 'synthetic-key'}})
        self.assertEqual(doc, before)
        self.assertEqual(saved['revision'], 1)
        self.assertTrue(saved['providers'][0]['hasCredential'])
        for forbidden in ('credentialRef', 'synthetic-key', '.key-'):
            self.assertNotIn(forbidden, json.dumps(saved))
        self.assertEqual(W.read_private(self.key_path()), b'synthetic-key')
        self.assertEqual(self.store.resolve_credential('tower', expected_revision=1), 'synthetic-key')
        self.assertNotIn(b'synthetic-key', W.read_private(self.directory / 'provider-config.json'))

    def test_false_hint_and_label_edit_preserve_reference(self):
        saved = self.seed()
        old = self.key_path()
        saved['providers'][0].update(label='Renamed', hasCredential=False)
        saved = self.save(saved)
        self.assertEqual(self.key_path(), old)
        self.assertTrue(saved['providers'][0]['hasCredential'])
        self.assertEqual(self.store.resolve_credential('tower', expected_revision=2), 'synthetic-key')

    def test_target_change_requires_explicit_replacement(self):
        saved = self.seed()
        old = self.key_path()
        saved['providers'][0]['baseUrl'] = 'https://new.example/v1'
        self.assertCode('credential-target-changed', lambda: self.save(saved))
        saved = self.save(saved, {'tower': {'action': 'set', 'value': 'replacement'}})
        self.assertNotEqual(self.key_path(), old)
        self.assertEqual(W.read_private(old), b'synthetic-key')
        self.assertEqual(self.store.resolve_credential('tower', expected_revision=2), 'replacement')

    def test_explicit_remove_keeps_old_key_and_permits_target_change(self):
        saved = self.seed()
        old = self.key_path()
        saved['providers'][0]['baseUrl'] = 'https://new.example/v1'
        saved = self.save(saved, {'tower': {'action': 'remove'}})
        self.assertFalse(saved['providers'][0]['hasCredential'])
        self.assertIsNone(self.store.resolve_credential('tower', expected_revision=2))
        self.assertEqual(W.read_private(old), b'synthetic-key')

    def test_cloud_presence_hint_is_not_authority(self):
        doc = document()
        doc['providers'][0].update(kind='cloud', hasCredential=True)
        self.assertCode('invalid-config', lambda: self.save(doc))
        self.assertEqual(list(self.directory.glob('.key-*.key')), [])
        saved = self.save(doc, {'tower': {'action': 'set', 'value': 'synthetic-cloud'}})
        saved['providers'][0]['hasCredential'] = False
        saved = self.save(saved)
        self.assertTrue(saved['providers'][0]['hasCredential'])
        self.assertEqual(self.store.resolve_credential('tower', expected_revision=2), 'synthetic-cloud')

    def test_new_identity_cannot_inherit_existing_secret(self):
        saved = self.seed()
        old = self.key_path()
        saved['providers'][0]['id'] = 'new-identity'
        saved = self.save(saved)
        self.assertFalse(saved['providers'][0]['hasCredential'])
        self.assertIsNone(self.store.resolve_credential('new-identity', expected_revision=2))
        self.assertEqual(W.read_private(old), b'synthetic-key')

    def test_stale_revision_cannot_publish_key(self):
        self.seed()
        before = W.read_private(self.directory / 'provider-config.json')
        keys = list(self.directory.glob('.key-*.key'))
        self.assertCode('stale-revision', lambda: self.seed())
        self.assertEqual(list(self.directory.glob('.key-*.key')), keys)
        self.assertEqual(W.read_private(self.directory / 'provider-config.json'), before)

    def test_key_validation_precedes_any_storage_mutation(self):
        for value in ('', 'x\ny', 'x' * 8193, 'not ascii \u00e9', 'space here', None):
            with self.subTest(value_type=type(value).__name__):
                self.assertCode('invalid-request', lambda: self.save(document(),
                                {'tower': {'action': 'set', 'value': value}}))
        self.assertEqual([item.name for item in self.directory.iterdir()], ['private.txt'])

    def test_client_cannot_supply_reference_or_extra_action_fields(self):
        doc = document()
        doc['providers'][0]['credentialRef'] = 'key-' + 'a' * 32
        self.assertCode('invalid-request', lambda: self.save(doc))
        self.assertCode('invalid-request', lambda: self.save(document(),
                        {'tower': {'action': 'remove', 'value': 'extra'}}))
        self.assertEqual([item.name for item in self.directory.iterdir()], ['private.txt'])

    def test_maximum_key_is_valid(self):
        self.save(document(), {'tower': {'action': 'set', 'value': 'x' * 8192}})
        self.assertEqual(self.store.resolve_credential('tower', expected_revision=1), 'x' * 8192)

    def test_random_collision_never_overwrites_old_key(self):
        with patch('pixel_provider.windows_vault.secrets.token_hex', return_value='a' * 32):
            saved = self.seed()
            self.assertCode('credential-write-failed', lambda: self.save(saved,
                            {'tower': {'action': 'set', 'value': 'replacement'}}))
        self.assertEqual(self.store.load()['revision'], 1)
        self.assertEqual(self.store.resolve_credential('tower', expected_revision=1), 'synthetic-key')

    def test_duplicate_pending_key_is_refused_before_publication(self):
        doc = document()
        doc['providers'].append(dict(doc['providers'][0], id='second'))
        changes = {name: {'action': 'set', 'value': 'synthetic-' + name} for name in ('tower', 'second')}
        with patch('pixel_provider.windows_vault.secrets.token_hex', return_value='b' * 32):
            self.assertCode('credential-write-failed', lambda: self.save(doc, changes))
        self.assertEqual(list(self.directory.glob('.key-*.key')), [])
        self.assertEqual(self.store.load()['revision'], 0)

    def test_multiple_providers_have_independent_immutable_keys(self):
        doc = document()
        doc['providers'].append(dict(doc['providers'][0], id='second'))
        saved = self.save(doc, {name: {'action': 'set', 'value': 'synthetic-' + name}
                               for name in ('tower', 'second')})
        self.assertEqual(len(list(self.directory.glob('.key-*.key'))), 2)
        for name in ('tower', 'second'):
            self.assertEqual(self.store.resolve_credential(name, expected_revision=1), 'synthetic-' + name)
        saved['providers'] = saved['providers'][:1]
        self.save(saved)
        self.assertEqual(len(list(self.directory.glob('.key-*.key'))), 2)
        self.assertCode('invalid-request', lambda: self.store.resolve_credential('second', expected_revision=2))

    def test_resolution_revision_and_identity_errors_are_not_swallowed(self):
        self.seed()
        for identity, revision, code in (('tower', 0, 'stale-revision'),
                                         ('tower', True, 'invalid-request'),
                                         (None, 1, 'invalid-request'), ('missing', 1, 'invalid-request')):
            self.assertCode(code, lambda: self.store.resolve_credential(identity, expected_revision=revision))

    def test_missing_and_malformed_key_are_content_free(self):
        self.seed()
        path = self.key_path()
        for content in (b'', b'bad\nkey', b'\xff', b'x' * 8193):
            path.write_bytes(content)  # Own synthetic fixture; retain its private ACL.
            error = self.assertCode('credential-unavailable',
                                    lambda: self.store.resolve_credential('tower', expected_revision=1))
            self.assertEqual(str(error), 'credential-unavailable')
        path.unlink()  # Exact single synthetic key created by this test.
        self.assertCode('credential-unavailable', lambda: self.store.resolve_credential('tower', expected_revision=1))

    def test_bad_reference_does_not_become_a_path(self):
        self.seed()
        raw = self.store.load()
        raw['providers'][0]['credentialRef'] = 'external-reference'
        with T.private_directory_transaction(self.directory) as tx:
            tx.replace('provider-config.json', json.dumps(raw).encode())
        self.assertCode('credential-unavailable', lambda: self.store.resolve_credential('tower', expected_revision=1))

    def test_key_acl_drift_and_hardlink_are_denied(self):
        self.seed()
        path = self.key_path()
        self.change_acl(path, f'D:P(A;;FA;;;{self.sid})(A;;FR;;;WD)')
        self.assertCode('credential-unavailable', lambda: self.store.resolve_credential('tower', expected_revision=1))
        self.change_acl(path, f'D:P(A;;FA;;;{self.sid})')
        os.link(path, self.directory / 'synthetic-hardlink')
        self.assertCode('credential-unavailable', lambda: self.store.resolve_credential('tower', expected_revision=1))

    def test_failed_key_flush_does_not_publish_config(self):
        api, calls = T._apis()
        calls['flush'] = lambda handle: False
        with patch.object(T, '_apis', return_value=(api, calls)):
            self.assertCode('credential-write-failed', lambda: self.seed())
        self.assertEqual(self.store.load()['revision'], 0)
        self.assertEqual(list(self.directory.glob('.key-*.key')), [])

    def test_failed_config_flush_retains_private_orphan_and_old_reference(self):
        saved = self.seed()
        old = self.key_path()
        api, calls = T._apis()
        original, count = calls['flush'], []
        def fail_second(handle):
            count.append(handle)
            return original(handle) if len(count) == 1 else False
        calls['flush'] = fail_second
        with patch.object(T, '_apis', return_value=(api, calls)):
            self.assertCode('write-failed', lambda: self.save(saved,
                            {'tower': {'action': 'set', 'value': 'replacement'}}))
        self.assertEqual(len(count), 2)
        self.assertEqual(self.store.load()['revision'], 1)
        self.assertEqual(self.key_path(), old)
        self.assertEqual(self.store.resolve_credential('tower', expected_revision=1), 'synthetic-key')
        self.assertCountEqual([W.read_private(path) for path in self.directory.glob('.key-*.key')],
                              [b'synthetic-key', b'replacement'])

    def test_second_key_failure_retains_first_private_key_without_config(self):
        doc = document()
        doc['providers'].append(dict(doc['providers'][0], id='second'))
        api, calls = T._apis()
        original, count = calls['flush'], []
        def fail_second(handle):
            count.append(handle)
            return original(handle) if len(count) == 1 else False
        calls['flush'] = fail_second
        with patch.object(T, '_apis', return_value=(api, calls)):
            self.assertCode('credential-write-failed', lambda: self.save(doc,
                            {name: {'action': 'set', 'value': 'synthetic-' + name}
                             for name in ('tower', 'second')}))
        self.assertEqual(len(count), 2)
        self.assertEqual(self.store.load()['revision'], 0)
        self.assertEqual([W.read_private(path) for path in self.directory.glob('.key-*.key')],
                         [b'synthetic-tower'])

    def test_uncertain_key_publication_retains_key_without_config(self):
        api, calls = T._apis()
        original = calls['move']
        def publish_then_fail(source, target, flags):
            self.assertTrue(original(source, target, flags))
            return False
        calls['move'] = publish_then_fail
        with patch.object(T, '_apis', return_value=(api, calls)):
            self.assertCode('write-durability-unknown', lambda: self.seed())
        self.assertEqual(self.store.load()['revision'], 0)
        self.assertEqual([W.read_private(path) for path in self.directory.glob('.key-*.key')], [b'synthetic-key'])

    def test_uncertain_config_publication_retains_valid_secret_reference(self):
        saved = self.seed()
        api, calls = T._apis()
        original, count = calls['move'], []
        def publish_then_fail_config(source, target, flags):
            count.append(target)
            result = original(source, target, flags)
            self.assertTrue(result)
            return result if len(count) == 1 else False
        calls['move'] = publish_then_fail_config
        with patch.object(T, '_apis', return_value=(api, calls)):
            self.assertCode('write-durability-unknown', lambda: self.save(saved,
                            {'tower': {'action': 'set', 'value': 'replacement'}}))
        self.assertEqual(len(count), 2)
        self.assertEqual(self.store.load()['revision'], 2)
        self.assertEqual(self.store.resolve_credential('tower', expected_revision=2), 'replacement')
        self.assertCountEqual([W.read_private(path) for path in self.directory.glob('.key-*.key')],
                              [b'synthetic-key', b'replacement'])

    def test_actual_two_process_credential_revision_race(self):
        children = [subprocess.Popen([sys.executable, __file__, '--peer', str(self.directory)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                     creationflags=subprocess.CREATE_NO_WINDOW) for _ in range(2)]
        outcomes = []
        try:
            for child in children:
                output, error = child.communicate(timeout=8)
                self.assertEqual(child.returncode, 0, error)
                outcomes.append(json.loads(output))
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()  # Exact owned test child, no unrelated processes.
                child.communicate(timeout=5)
        self.assertCountEqual(outcomes, [{'saved': 1}, {'error': 'stale-revision'}])
        self.assertEqual(len(list(self.directory.glob('.key-*.key'))), 1)
        self.assertEqual(self.store.resolve_credential('tower', expected_revision=1), 'synthetic-key')


@unittest.skipIf(os.name == 'nt', 'Non-Windows explicit adapter refusal')
class UnsupportedCredentials(unittest.TestCase):
    def test_import_is_safe_but_storage_operations_refuse(self):
        store = WindowsCredentialStore('/not-created-by-this-test')
        for call in (store.load, store.read_snapshot,
                     lambda: store.resolve_credential('tower', expected_revision=0),
                     lambda: store.save_public(dict(document=document(), expectedRevision=0))):
            with self.assertRaises(StoreError) as context:
                call()
            self.assertEqual(context.exception.code, 'unsupported-platform')


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--peer':
        try:
            saved = WindowsCredentialStore(sys.argv[2]).save_public(dict(document=document(),
                expectedRevision=0, credentialChanges={'tower': {'action': 'set', 'value': 'synthetic-key'}}))
            print(json.dumps({'saved': saved['revision']}))
        except StoreError as error:
            print(json.dumps({'error': error.code}))
    else:
        unittest.main()
