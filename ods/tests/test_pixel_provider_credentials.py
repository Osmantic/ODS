import json
import os
import sys
import tempfile
import unittest
import copy
import threading
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'bin'))
from pixel_provider.vault import CredentialStore, validate_edit
from pixel_provider.config import default_config
from pixel_provider.store import StoreError

@unittest.skipUnless(os.name=='posix','POSIX credential custody')
class Credentials(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)
        self.directory.chmod(0o700)
        self.store=CredentialStore(self.directory)
    def document(self):
        result=default_config()
        result['providers']=[dict(id='tower',label='Tower',kind='ods-peer',baseUrl='https://tower.example/v1',model='model',contextTokens=32768,maxOutputTokens=4096,supportsTools=True,supportsVision=False,reasoning=False,hasCredential=False,enabled=True)]
        return result
    def save(self, doc, changes=None):
        body=dict(expectedRevision=doc['revision'],document=doc)
        if changes is not None: body['credentialChanges']=changes
        return self.store.save_public(body)
    def seed(self):
        return self.save(self.document(),{'tower':{'action':'set','value':'synthetic-key'}})
    def key_path(self):
        ref=self.store.load()['providers'][0]['credentialRef']
        return self.directory/('.'+ref+'.key')

    def test_01_seed_returns_full_public_document(self):
        saved = self.seed()
        self.assertIsInstance(saved, dict)
        self.assertEqual(saved['revision'], 1)
        self.assertTrue(saved['providers'][0]['hasCredential'])
        json_str = json.dumps(saved)
        self.assertNotIn('credentialRef', json_str)
        self.assertNotIn('synthetic-key', json_str)
        self.assertEqual(self.store.resolve_credential('tower', expected_revision=1), 'synthetic-key')
        self.assertEqual(self.key_path().stat().st_mode & 0o777, 0o600)

    def test_02_rename_preserves_credential_ref(self):
        saved = self.seed()
        old_ref = self.store.load()['providers'][0]['credentialRef']
        saved['providers'][0]['label'] = 'Renamed'
        saved['providers'][0]['hasCredential'] = False
        result = self.save(saved)
        self.assertEqual(result['revision'], 2)
        self.assertEqual(self.store.load()['providers'][0]['credentialRef'], old_ref)
        self.assertEqual(self.store.resolve_credential('tower', expected_revision=2), 'synthetic-key')

    def test_03_stale_revision_raises_error(self):
        self.seed()
        config_bytes = (self.directory / 'provider-config.json').read_bytes()
        key_files = sorted([p.name for p in self.directory.glob('.*.key')])
        stale = self.document()
        stale['revision'] = 0
        with self.assertRaises(StoreError) as ctx:
            self.save(stale, {'tower': {'action': 'set', 'value': 'replacement'}})
        self.assertEqual(ctx.exception.code, 'stale-revision')
        self.assertEqual((self.directory / 'provider-config.json').read_bytes(), config_bytes)
        self.assertEqual(sorted([p.name for p in self.directory.glob('.*.key')]), key_files)

    def test_04_target_change_requires_explicit_credential_update(self):
        saved = self.seed()
        old_path = self.key_path()
        saved['providers'][0]['baseUrl'] = 'https://new.example/v1'
        with self.assertRaises(StoreError) as ctx:
            self.save(saved)
        self.assertEqual(ctx.exception.code, 'credential-target-changed')

        result = self.save(saved, {'tower': {'action': 'set', 'value': 'replacement'}})
        self.assertEqual(result['revision'], 2)
        self.assertEqual(self.store.resolve_credential('tower', expected_revision=2), 'replacement')
        self.assertTrue(old_path.exists())

    def test_05_remove_credential_retains_file(self):
        saved = self.seed()
        old_path = self.key_path()
        result = self.save(saved, {'tower': {'action': 'remove'}})
        self.assertFalse(result['providers'][0]['hasCredential'])
        self.assertIsNone(self.store.resolve_credential('tower', expected_revision=2))
        self.assertTrue(old_path.exists())

    def test_06_spoofed_credential_rejected(self):
        doc = self.document()
        doc['providers'][0]['kind'] = 'cloud'
        doc['providers'][0]['hasCredential'] = True
        with self.assertRaises(StoreError):
            self.save(doc)
        key_files = list(self.directory.glob('.key-*.key'))
        self.assertEqual(len(key_files), 0)

    def test_preflight_missing_revision_and_corrupt_secret_are_content_free(self):
        with self.assertRaises(StoreError):
            validate_edit({'expectedRevision': 0, 'document': {'providers': []}})
        self.seed()
        self.key_path().write_bytes(b'x' * 8193)
        with self.assertRaises(StoreError) as exc:
            self.store.resolve_credential('tower', expected_revision=1)
        self.assertEqual(exc.exception.code, 'credential-unavailable')

    def test_invalid_actions_and_keys_never_write(self):
        for action in ({'action': 'set', 'value': ''}, {'action': 'set', 'value': 'x\ny'},
                       {'action': 'set', 'value': 'x' * 8193}, {'action': 'move'},
                       {'action': 'remove', 'value': 'extra'}):
            with self.subTest(action=action['action']), self.assertRaises(StoreError):
                self.save(self.document(), {'tower': action})
        for change in ({'unknown': {'action': 'remove'}}, {'tower': []}):
            with self.assertRaises(StoreError):
                self.save(self.document(), change)
        doc = self.document()
        doc['providers'][0]['credentialRef'] = 'forbidden-client-ref'
        with self.assertRaises(StoreError):
            self.save(doc)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_new_identity_does_not_inherit_and_false_hint_keeps_cloud_key(self):
        saved = self.seed()
        saved['providers'][0]['kind'] = 'cloud'
        saved = self.save(saved, {'tower': {'action': 'set', 'value': 'synthetic-cloud'}})
        saved['providers'][0]['hasCredential'] = False
        saved = self.save(saved)
        self.assertTrue(saved['providers'][0]['hasCredential'])
        saved['providers'][0].update(id='new-identity', kind='ods-peer')
        saved = self.save(saved)
        self.assertFalse(saved['providers'][0]['hasCredential'])
        self.assertIsNone(self.store.resolve_credential('new-identity', expected_revision=4))

    def test_fsync_failures_preserve_reference_atomicity(self):
        # key fsync, key directory fsync, config fsync, config directory fsync.
        for failure in range(1, 5):
            with self.subTest(fsync=failure), tempfile.TemporaryDirectory() as directory:
                store = CredentialStore(directory)
                calls = []
                original = os.fsync
                def fail(fd):
                    calls.append(fd)
                    if len(calls) == failure:
                        raise OSError('synthetic fault')
                    return original(fd)
                with patch('os.fsync', side_effect=fail), self.assertRaises(StoreError) as exc:
                    store.save_public({'expectedRevision': 0, 'document': self.document(),
                                       'credentialChanges': {'tower': {'action': 'set', 'value': 'synthetic-key'}}})
                paths = list(Path(directory).glob('.key-*.key'))
                if failure == 4:
                    self.assertEqual(exc.exception.code, 'write-durability-unknown')
                    self.assertEqual(store.load()['revision'], 1)
                    self.assertEqual(store.resolve_credential('tower', expected_revision=1), 'synthetic-key')
                    self.assertEqual(len(paths), 1)
                else:
                    self.assertEqual(store.load()['revision'], 0)
                    self.assertEqual(paths, [])

    def test_concurrent_key_set_has_one_winner_and_no_orphan(self):
        barrier = threading.Barrier(2)
        results = []
        def writer():
            barrier.wait(timeout=2)
            try:
                self.seed()
                results.append('ok')
            except StoreError as exc:
                results.append(exc.code)
        threads = [threading.Thread(target=writer) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=6)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(results, ['ok', 'stale-revision'])
        self.assertEqual(len(list(self.directory.glob('.key-*.key'))), 1)

    def test_custody_missing_and_bad_revision_fail_closed(self):
        self.seed()
        path = self.key_path()
        original = path.read_bytes()
        with self.assertRaises(StoreError) as exc:
            self.store.resolve_credential('tower', expected_revision=0)
        self.assertEqual(exc.exception.code, 'stale-revision')
        for mode in ('symlink', 'hardlink', 'fifo', 'missing'):
            with self.subTest(mode=mode):
                path.unlink()
                target = self.directory / ('target-' + mode)
                target.write_bytes(original)
                target.chmod(0o600)
                if mode == 'symlink':
                    path.symlink_to(target)
                elif mode == 'hardlink':
                    os.link(target, path)
                elif mode == 'fifo':
                    os.mkfifo(path, 0o600)
                with self.assertRaises(StoreError) as exc:
                    self.store.resolve_credential('tower', expected_revision=1)
                self.assertEqual(exc.exception.code, 'credential-unavailable')
                if mode != 'missing':
                    path.unlink()
                path.write_bytes(original)
                path.chmod(0o600)

    def test_input_is_unchanged_and_secret_collision_does_not_overwrite(self):
        doc = self.document()
        original = copy.deepcopy(doc)
        with patch('pixel_provider.vault.secrets.token_hex', return_value='a' * 32):
            saved = self.save(doc, {'tower': {'action': 'set', 'value': 'first-key'}})
            with self.assertRaises(StoreError):
                self.save(saved, {'tower': {'action': 'set', 'value': 'second-key'}})
        self.assertEqual(doc, original)
        self.assertEqual(self.store.resolve_credential('tower', expected_revision=1), 'first-key')
