"""Real native loopback AgentHandler HTTP; synthetic state, no installed agent."""
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import test_pixel_provider_windows_custody as fixtures
import test_pixel_provider_host_api as http_fixtures
import test_pixel_provider_windows_credentials as credential_fixtures
from pixel_provider import windows_bootstrap as B
from pixel_provider import windows_custody as W
from pixel_provider import windows_transactions as T
from pixel_provider.config import default_config
from pixel_provider.windows_vault import WindowsCredentialStore


@unittest.skipUnless(os.name == 'nt', 'Actual native Windows provider HTTP')
class NativeHostHTTP(unittest.TestCase):
    descriptor = fixtures.NativeCustody.descriptor
    create = fixtures.NativeCustody.create
    change_acl = fixtures.NativeCustody.change_acl
    request = http_fixtures.HostHTTP.request

    @classmethod
    def setUpClass(cls):
        fixtures.NativeCustody.setUpClass.__func__(cls)
        http_fixtures.HostHTTP.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        http_fixtures.HostHTTP.tearDownClass.__func__(cls)

    def setUp(self):
        fixtures.NativeCustody.setUp(self)
        self.agent.DATA_DIR = self.directory
        self.provider_root = self.directory / 'pixel-providers'

    def save(self, document=None, *, changes=None):
        document = default_config() if document is None else document
        body = dict(document=document, expectedRevision=document['revision'])
        if changes is not None:
            body['credentialChanges'] = changes
        return self.request('POST', body)

    def seed(self):
        status, saved = self.save(credential_fixtures.document(),
                                 changes={'tower': {'action': 'set', 'value': 'synthetic-key'}})
        self.assertEqual(status, 200, saved)
        return saved

    def assert_pristine(self):
        self.assertFalse(self.provider_root.exists())
        self.assertEqual(self.file.read_bytes(), b'fictional fixture')

    def test_pristine_get_returns_disabled_default_without_creating_state(self):
        self.assertEqual(self.request(), (200, default_config()))
        self.assert_pristine()

    def test_authentication_precedes_state_and_validation(self):
        for method in ('GET', 'POST'):
            self.assertEqual(self.request(method, {}, token='wrong')[0], 403)
        self.assert_pristine()

    def test_separate_scope_api_is_not_silently_native_enabled(self):
        self.assertEqual(self.request(), (200, default_config()))
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            connection.request('POST', '/v1/pixel/provider-scopes/status',
                               body=json.dumps({'chatId': 'native-not-qualified'}),
                               headers={'Authorization': 'Bearer synthetic-provider-test-key',
                                        'Content-Type': 'application/json'})
            response = connection.getresponse()
            self.assertEqual(response.status, 400)  # Existing scope API's non-conflict StoreError mapping.
            self.assertEqual(json.loads(response.read())['code'], 'unsupported-platform')
        finally:
            connection.close()
        self.assert_pristine()

    def test_first_save_reload_and_stale_revision_use_real_native_custody(self):
        status, saved = self.save()
        self.assertEqual(status, 200, saved)
        self.assertEqual(saved['revision'], 1)
        self.assertFalse(saved['enabled'])
        self.assertFalse(saved['policy']['allowCloud'])
        self.assertEqual(self.request(), (200, saved))
        self.assertEqual(self.save()[0], 409)
        for path in self.provider_root.iterdir():
            with W.open_private(path):
                pass
        self.assertEqual(json.loads(W.read_private(self.provider_root / 'provider-config.json')), saved)

    def test_secret_projection_false_hint_and_retarget_guard(self):
        saved = self.seed()
        store = WindowsCredentialStore(self.provider_root)
        key = next(self.provider_root.glob('.key-*.key'))
        self.assertEqual(W.read_private(key), b'synthetic-key')
        self.assertEqual(self.request(), (200, saved))
        for forbidden in ('synthetic-key', 'credentialRef', '.key-'):
            self.assertNotIn(forbidden, json.dumps(saved))
        saved['providers'][0].update(label='Edited', hasCredential=False)
        status, saved = self.save(saved)
        self.assertEqual(status, 200, saved)
        self.assertTrue(saved['providers'][0]['hasCredential'])
        self.assertEqual(store.resolve_credential('tower', expected_revision=2), 'synthetic-key')
        saved['providers'][0]['baseUrl'] = 'https://replacement.invalid/v1'
        status, error = self.save(saved)
        self.assertEqual(status, 400, error)
        self.assertEqual(error['code'], 'credential-target-changed')
        self.assertEqual(store.load()['revision'], 2)
        status, saved = self.save(saved, changes={'tower': {'action': 'remove'}})
        self.assertEqual(status, 200, saved)
        self.assertFalse(saved['providers'][0]['hasCredential'])
        self.assertIsNone(store.resolve_credential('tower', expected_revision=3))
        self.assertEqual(W.read_private(key), b'synthetic-key')  # Removal is not key-file collection.

    def test_cloud_settings_do_not_enable_cloud_or_runtime(self):
        document = credential_fixtures.document()
        document['providers'][0]['kind'] = 'cloud'
        status, saved = self.save(document, changes={'tower': {'action': 'set', 'value': 'synthetic-cloud'}})
        self.assertEqual(status, 200, saved)
        self.assertTrue(saved['providers'][0]['hasCredential'])
        self.assertFalse(saved['enabled'])
        self.assertFalse(saved['policy']['allowCloud'])
        self.assertEqual(self.request(), (200, saved))

    def test_invalid_payloads_and_credentials_never_create_state(self):
        for raw in (b'{"expectedRevision":0,"expectedRevision":1,"document":{}}',
                    b'NaN', b'{"expectedRevision":true,"document":{}}',
                    b'{"expectedRevision":0,"document":{"secret":"do-not-echo"}}',
                    b'{"expectedRevision":0,"document":{"x":1e999}}'):
            status, error = self.request('POST', raw=raw)
            self.assertEqual(status, 400, error)
            self.assertNotIn('do-not-echo', json.dumps(error))
        for value in ('', 'with space', 'line\nbreak', 'x' * 8193, None):
            self.assertEqual(self.save(credential_fixtures.document(),
                             changes={'tower': {'action': 'set', 'value': value}})[0], 400)
        self.assert_pristine()

    def test_oversize_and_ambiguous_framing_reject_before_reading_body(self):
        for fields, expected in (([('Content-Length', str(256 * 1024 + 1))], 413),
                                 ([('Content-Length', '0')], 400),
                                 ([('Content-Length', '2'), ('Content-Length', '2')], 400),
                                 ([('Transfer-Encoding', 'chunked')], 400)):
            connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
            try:
                connection.putrequest('POST', '/v1/pixel/providers/save')
                connection.putheader('Authorization', 'Bearer synthetic-provider-test-key')
                for name, value in fields:
                    connection.putheader(name, value)
                connection.endheaders()  # Intentionally no untrusted payload body.
                response = connection.getresponse()
                self.assertEqual(response.status, expected)
                self.assertIn('error', json.loads(response.read()))
            finally:
                connection.close()
        self.assert_pristine()

    def test_eager_oversize_is_rejected_without_state(self):
        # Windows may reset a connection when the server rejects its headers and
        # closes while the client is still eagerly writing the oversized body.
        # The header-only test separately proves the precise 413 response.
        try:
            self.assertEqual(self.request('POST', raw=b'x' * (256 * 1024 + 1))[0], 413)
        except (ConnectionAbortedError, ConnectionResetError):
            pass
        self.assert_pristine()

    def test_unsafe_existing_root_is_not_repaired_or_defaulted(self):
        self.create(self.provider_root, directory=True,
                    acl=f'D:P(A;;FA;;;{self.sid})(A;;FR;;;WD)')
        sentinel = self.create(self.provider_root / 'sentinel', content=b'keep me')
        for call in (self.request, self.save):
            status, error = call()
            self.assertEqual(status, 503, error)
            self.assertEqual(error['code'], 'storage-unavailable')
        self.assertEqual(sentinel.read_bytes(), b'keep me')
        self.assertEqual([path.name for path in self.provider_root.iterdir()], ['sentinel'])
        with self.assertRaises(W.WindowsCustodyError):
            with W.open_private(self.provider_root, directory=True):
                self.fail('Unsafe root was adopted or repaired')

    def test_raw_ambiguous_data_paths_refuse_without_creation(self):
        for value in (str(self.directory) + '\\.', str(self.directory) + '\\..',
                      str(self.directory) + '\\', 'relative', r'\\server\share\store'):
            self.agent.DATA_DIR = value
            for call in (self.request, self.save):
                status, error = call()
                self.assertEqual(status, 503, error)
                self.assertEqual(error['code'], 'storage-unavailable')
        self.assert_pristine()

    def test_missing_ancestor_is_not_a_pristine_install(self):
        self.agent.DATA_DIR = self.directory / 'missing'
        self.assertEqual(self.request()[0], 503)
        self.assertEqual(self.save()[0], 503)
        self.assertFalse(self.agent.DATA_DIR.exists())

    def test_junction_data_directory_is_refused_without_target_mutation(self):
        junction = self.directory / 'junction'
        target = self.create(self.directory / 'target', directory=True)
        result = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(junction), str(target)],
                                capture_output=True, text=True, timeout=10,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.agent.DATA_DIR = junction
        self.assertEqual(self.request()[0], 503)
        self.assertEqual(self.save()[0], 503)
        self.assertEqual(list(target.iterdir()), [])

    def test_corrupt_disk_refuses_without_echo_or_overwrite(self):
        self.seed()
        config = self.provider_root / 'provider-config.json'
        config.write_bytes(b'{"secret":"do-not-echo"}')  # Only owned synthetic state.
        for call in (self.request, self.save):
            status, error = call()
            self.assertEqual(status, 503 if call == self.request else 400, error)
            self.assertNotIn('do-not-echo', json.dumps(error))
        self.assertEqual(config.read_bytes(), b'{"secret":"do-not-echo"}')

    def test_concurrent_http_saves_have_one_revision_winner_and_one_key(self):
        def save():
            return self.save(credential_fixtures.document(),
                             changes={'tower': {'action': 'set', 'value': 'synthetic-key'}})
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(save) for _ in range(2)]
            outcomes = [future.result(timeout=8) for future in futures]
        self.assertCountEqual([status for status, _ in outcomes], [200, 409])
        self.assertEqual(self.request()[1]['revision'], 1)
        self.assertEqual(len(list(self.provider_root.glob('.key-*.key'))), 1)

    def test_real_creation_then_exception_is_unknown_and_reloadable(self):
        original = B._create_api()
        def fail_after(*args):
            self.assertEqual(original(*args), 0)
            raise OSError('synthetic do-not-echo after actual creation')
        with patch.object(B, '_create_api', return_value=fail_after):
            status, error = self.save()
        self.assertEqual(status, 503, error)
        self.assertEqual(error['code'], 'write-durability-unknown')
        self.assertNotIn('do-not-echo', json.dumps(error))
        with W.open_private(self.provider_root, directory=True):
            pass
        self.assertEqual(list(self.provider_root.iterdir()), [])
        self.assertEqual(self.request(), (200, default_config()))
        self.assertEqual(self.save()[0], 200)

    def test_prepublication_flush_failure_leaves_prior_revision(self):
        saved = self.seed()
        api, calls = T._apis()
        calls['flush'] = lambda handle: False
        with patch.object(T, '_apis', return_value=(api, calls)):
            status, error = self.save(saved)
        self.assertEqual(status, 503, error)
        self.assertEqual(error['code'], 'write-failed')
        self.assertEqual(self.request(), (200, saved))

    def test_real_config_publish_then_failure_reloads_committed_revision(self):
        saved = self.seed()
        api, calls = T._apis()
        original, targets = calls['move'], []
        def fail_after_config(source, target, flags):
            targets.append(target)
            result = original(source, target, flags)
            self.assertTrue(result)
            return result if len(targets) == 1 else False
        calls['move'] = fail_after_config
        with patch.object(T, '_apis', return_value=(api, calls)):
            status, error = self.save(saved, changes={'tower': {'action': 'set', 'value': 'replacement'}})
        self.assertEqual(status, 503, error)
        self.assertEqual(error['code'], 'write-durability-unknown')
        self.assertEqual(len(targets), 2)
        status, current = self.request()
        self.assertEqual(status, 200, current)
        self.assertEqual(current['revision'], 2)
        self.assertTrue(current['providers'][0]['hasCredential'])
        self.assertEqual(WindowsCredentialStore(self.provider_root).resolve_credential(
                         'tower', expected_revision=2), 'replacement')
        self.assertEqual(self.save(saved)[0], 409)  # Blind retry cannot duplicate publication.
        self.assertCountEqual([W.read_private(path) for path in self.provider_root.glob('.key-*.key')],
                              [b'synthetic-key', b'replacement'])

    def test_separate_native_process_reopens_through_real_http(self):
        saved = self.seed()
        result = subprocess.run([sys.executable, __file__, '--reopen-http', str(self.directory)],
                                capture_output=True, text=True, timeout=10,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [200, saved])
        self.assertNotIn('synthetic-key', result.stdout + result.stderr)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--reopen-http':
        runner = type('ReopenHTTP', (), {'request': http_fixtures.HostHTTP.request})
        http_fixtures.HostHTTP.setUpClass.__func__(runner)
        try:
            runner.agent.DATA_DIR = Path(sys.argv[2])
            print(json.dumps(runner().request()))
        finally:
            http_fixtures.HostHTTP.tearDownClass.__func__(runner)
    else:
        unittest.main()
