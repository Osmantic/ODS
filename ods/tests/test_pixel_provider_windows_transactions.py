"""Actual NTFS locking/publication tests; all state is synthetic and retained."""
import ctypes as C
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import test_pixel_provider_windows_custody as fixtures
from pixel_provider import windows_custody as W
from pixel_provider import windows_transactions as T
from pixel_provider.store import StoreError
from pixel_provider.windows_store import WindowsProviderStore


@unittest.skipUnless(os.name == 'nt', 'Requires actual native Windows APIs')
class Transactions(unittest.TestCase):
    setUpClass = classmethod(fixtures.NativeCustody.setUpClass.__func__)
    setUp = fixtures.NativeCustody.setUp
    descriptor = fixtures.NativeCustody.descriptor
    create = fixtures.NativeCustody.create
    change_acl = fixtures.NativeCustody.change_acl

    def peer(self, mode, *, exclusive=True):
        result = subprocess.run([sys.executable, __file__, '--peer', str(self.directory),
                                 mode, 'exclusive' if exclusive else 'shared'],
                                capture_output=True, text=True, timeout=8,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_write_read_replace_and_immutable_collision(self):
        with T.private_directory_transaction(self.directory) as tx:
            first = tx.create_immutable('new.json', b'one')
            self.assertEqual(tx.read('new.json'), b'one')
            with self.assertRaises(W.WindowsCustodyError) as context:
                tx.create_immutable('new.json', b'overwrite')
            self.assertEqual(context.exception.code, 'file-exists')
            self.assertEqual(tx.read('new.json'), b'one')
            second = tx.replace('new.json', b'two')
            self.assertNotEqual(first, second)
            self.assertEqual(tx.read('new.json'), b'two')
            self.assertIsNone(tx.read('missing.json'))
        self.assertEqual(W.read_private(self.directory / 'new.json'), b'two')

    def test_shared_transactions_deny_mutation(self):
        with T.private_directory_transaction(self.directory, exclusive=False) as tx:
            self.assertEqual(tx.read('private.txt'), b'fictional fixture')
            for method in (tx.replace, tx.create_immutable):
                with self.assertRaises(W.WindowsCustodyError):
                    method('new.json', b'not-written')
        self.assertFalse((self.directory / 'new.json').exists())

    def test_object_invalid_after_exit(self):
        with T.private_directory_transaction(self.directory) as tx:
            pass
        for method, args in ((tx.read, ('private.txt',)), (tx.replace, ('private.txt', b'bad'))):
            with self.assertRaises(W.WindowsCustodyError):
                method(*args)

    def test_transaction_cannot_cross_threads(self):
        outcomes = []
        with T.private_directory_transaction(self.directory) as tx:
            def other_thread():
                try:
                    tx.read('private.txt')
                except W.WindowsCustodyError as error:
                    outcomes.append(error.code)
            worker = threading.Thread(target=other_thread)
            worker.start()
            worker.join(2)
            self.assertFalse(worker.is_alive())
        self.assertEqual(outcomes, ['invalid-transaction'])

    def test_invalid_limits_rejected_even_for_missing_files(self):
        for value in (False, -1, float('nan'), 61):
            with self.assertRaises(W.WindowsCustodyError):
                with T.private_directory_transaction(self.directory, timeout=value):
                    self.fail('Invalid timeout admitted')
        with T.private_directory_transaction(self.directory) as tx:
            for value in (False, 0, -1, 1048577):
                with self.assertRaises(W.WindowsCustodyError):
                    tx.read('missing.json', max_bytes=value)

    def test_two_process_revision_race_has_one_winner(self):
        children = [subprocess.Popen([sys.executable, __file__, '--peer', str(self.directory),
                                     'save', 'exclusive'], stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True,
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
                    child.kill()
                child.communicate(timeout=5)
        self.assertCountEqual(outcomes, [{'saved': 1}, {'error': 'stale-revision'}])
        self.assertEqual(WindowsProviderStore(self.directory).load()['revision'], 1)

    def test_reserved_and_ambiguous_names(self):
        with T.private_directory_transaction(self.directory) as tx:
            for name in (T.LOCK_NAME, T.LOCK_NAME.upper(), '.ods-txn-forged.tmp', '../x',
                         'a/b', r'a\b', '.', '..', 'file:stream', 'NUL.txt'):
                with self.subTest(name=name), self.assertRaises(W.WindowsCustodyError):
                    tx.replace(name, b'bad')

    def test_actual_cross_process_exclusive_lock(self):
        started = time.monotonic()
        with T.private_directory_transaction(self.directory) as tx:
            identity = tx.lock_identity
            self.assertEqual(self.peer('acquire'), {'error': 'lock-timeout'})
            self.assertEqual(self.peer('acquire', exclusive=False), {'error': 'lock-timeout'})
            with self.assertRaises(OSError):
                os.rename(self.directory / T.LOCK_NAME, self.directory / 'changed-lock')
        self.assertLess(time.monotonic() - started, 5)
        with T.private_directory_transaction(self.directory) as tx:
            self.assertEqual(tx.lock_identity, identity)

    def test_actual_cross_process_shared_lock(self):
        with T.private_directory_transaction(self.directory, exclusive=False):
            self.assertEqual(self.peer('acquire', exclusive=False), {'acquired': True})
            self.assertEqual(self.peer('acquire'), {'error': 'lock-timeout'})

    def test_no_lock_upgrade_or_reentrancy(self):
        with T.private_directory_transaction(self.directory, exclusive=False):
            with self.assertRaises(W.WindowsCustodyError) as context:
                with T.private_directory_transaction(self.directory, timeout=0.025):
                    self.fail('Upgrade admitted')
            self.assertEqual(context.exception.code, 'lock-timeout')

    def test_process_kill_releases_stable_lock(self):
        ready = self.directory / 'peer-ready.json'
        peer = subprocess.Popen([sys.executable, __file__, '--peer', str(self.directory),
                                 'hold', 'exclusive'], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < deadline and peer.poll() is None:
                time.sleep(0.025)
            self.assertTrue(ready.exists(), 'Child did not acquire lock')
            self.assertEqual(json.loads(ready.read_text())['pid'], peer.pid)
            self.assertEqual(self.peer('acquire'), {'error': 'lock-timeout'})
            peer.kill()  # Exact owned test subprocess only.
            peer.communicate(timeout=5)
            with T.private_directory_transaction(self.directory, timeout=2):
                pass
        finally:
            if peer.poll() is None:
                peer.kill()
            peer.communicate(timeout=5)

    def test_raw_read_handle_prevents_replace_without_changing_bytes(self):
        with self.assertRaises(W.WindowsCustodyError) as outcome:
            with T.private_directory_transaction(self.directory) as tx:
                with W.open_private(self.file):
                    # The raw reader denies deletion. An attempted publication
                    # still latches unknown until the caller reconciles state.
                    with self.assertRaises(W.WindowsCustodyError):
                        tx.replace('private.txt', b'bad')
        self.assertEqual(outcome.exception.code, 'write-durability-unknown')
        self.assertEqual(W.read_private(self.file), b'fictional fixture')

    def test_shared_transactions_read_latest_complete_revision(self):
        store = WindowsProviderStore(self.directory)
        initial = store.load()
        with T.private_directory_transaction(self.directory, exclusive=False):
            self.assertEqual(self.peer('acquire', exclusive=False), {'acquired': True})
        saved = store.save(initial, expected_revision=0)
        with T.private_directory_transaction(self.directory, exclusive=False) as tx:
            self.assertEqual(json.loads(tx.read('provider-config.json')), saved)

    def test_zero_progress_write_and_cleanup_failure_retain_old_state(self):
        with T.private_directory_transaction(self.directory) as tx:
            with patch.dict(tx.calls, {'write': lambda *args: True, 'disposition': lambda *args: False}):
                with self.assertRaises(W.WindowsCustodyError):
                    tx.replace('private.txt', b'not-written')
            self.assertEqual(tx.read('private.txt'), b'fictional fixture')
            leftovers = list(self.directory.glob('.ods-txn-*'))
            self.assertEqual(len(leftovers), 1)
            self.assertEqual(W.read_private(leftovers[0]), b'')

    def test_published_acl_drift_retains_file_and_reports_unknown(self):
        with self.assertRaises(W.WindowsCustodyError) as outcome:
            with T.private_directory_transaction(self.directory) as tx:
                original = tx.calls['move']
                def changed(source, target, flags):
                    result = original(source, target, flags)
                    self.assertTrue(result)
                    self.change_acl(Path(target), f'D:P(A;;FA;;;{self.sid})(A;;FR;;;WD)')
                    return result
                with patch.dict(tx.calls, {'move': changed}):
                    tx.replace('private.txt', b'published before ACL drift')
        self.assertEqual(outcome.exception.code, 'write-durability-unknown')
        self.assertEqual(self.file.read_bytes(), b'published before ACL drift')

    def test_existing_target_hardlink_not_overwritten(self):
        linked = self.directory / 'linked.txt'
        os.link(self.file, linked)
        with T.private_directory_transaction(self.directory) as tx:
            with self.assertRaises(W.WindowsCustodyError):
                tx.replace('private.txt', b'bad')
        self.assertEqual(linked.read_bytes(), b'fictional fixture')

    def test_known_flush_failure_preserves_old_file(self):
        with T.private_directory_transaction(self.directory) as tx:
            with patch.dict(tx.calls, {'flush': lambda handle: False}):
                with self.assertRaises(W.WindowsCustodyError) as error:
                    tx.replace('private.txt', b'not-committed')
                self.assertEqual(error.exception.code, 'write-failed')
            self.assertEqual(tx.read('private.txt'), b'fictional fixture')
        self.assertEqual(list(self.directory.glob('.ods-txn-*')), [])

    def test_raising_cleanup_does_not_replace_original_write_error(self):
        def cleanup_failure(*args):
            raise RuntimeError('injected cleanup exception')
        with T.private_directory_transaction(self.directory) as tx:
            with patch.dict(tx.calls, {'flush': lambda handle: False, 'disposition': cleanup_failure}):
                with self.assertRaises(W.WindowsCustodyError) as error:
                    tx.replace('private.txt', b'not-committed')
                self.assertEqual(error.exception.code, 'write-failed')
        self.assertEqual(W.read_private(self.file), b'fictional fixture')
        self.assertEqual(len(list(self.directory.glob('.ods-txn-*'))), 1)

    def test_oversize_config_has_existing_malformed_json_code(self):
        from pixel_provider.store import MAX_BYTES
        with T.private_directory_transaction(self.directory) as tx:
            tx.create_immutable('provider-config.json', b' ' * (MAX_BYTES + 1))
        with self.assertRaises(StoreError) as error:
            WindowsProviderStore(self.directory).load()
        self.assertEqual(error.exception.code, 'malformed-json')

    def test_short_writes_are_completed(self):
        with T.private_directory_transaction(self.directory) as tx:
            original = tx.calls['write']
            calls = []
            def short(handle, buffer, count, written, overlapped):
                calls.append(count)
                return original(handle, buffer, min(count, 3), written, overlapped)
            with patch.dict(tx.calls, {'write': short}):
                tx.replace('private.txt', b'complete despite short writes')
            self.assertGreater(len(calls), 2)
            self.assertEqual(tx.read('private.txt'), b'complete despite short writes')

    def test_false_failure_after_real_publication_is_unknown(self):
        with self.assertRaises(W.WindowsCustodyError) as outer:
            with T.private_directory_transaction(self.directory) as tx:
                original = tx.calls['move']
                def uncertain(source, target, flags):
                    self.assertTrue(original(source, target, flags))
                    C.set_last_error(5)
                    return False
                with patch.dict(tx.calls, {'move': uncertain}):
                    with self.assertRaises(W.WindowsCustodyError) as inner:
                        tx.replace('private.txt', b'published but unknown')
                    self.assertEqual(inner.exception.code, 'write-durability-unknown')
                with self.assertRaises(W.WindowsCustodyError):
                    tx.replace('second.json', b'not-admitted')
        self.assertEqual(outer.exception.code, 'write-durability-unknown')
        self.assertEqual(W.read_private(self.file), b'published but unknown')
        self.assertFalse((self.directory / 'second.json').exists())

    def test_directory_acl_drift_blocks_before_write(self):
        with self.assertRaises(W.WindowsCustodyError):
            with T.private_directory_transaction(self.directory) as tx:
                self.change_acl(self.directory, f'D:P(A;;FA;;;{self.sid})(A;;FR;;;WD)')
                tx.replace('never.json', b'never-written')
        self.assertFalse((self.directory / 'never.json').exists())

    def test_existing_unsafe_lock_and_target_are_not_repaired(self):
        path = self.directory / T.LOCK_NAME
        path.write_bytes(b'unsafe inherited lock')
        with self.assertRaises(W.WindowsCustodyError):
            with T.private_directory_transaction(self.directory):
                self.fail('Unsafe lock accepted')
        self.assertEqual(path.read_bytes(), b'unsafe inherited lock')

    def test_real_provider_config_roundtrip_and_revision_cas(self):
        store = WindowsProviderStore(self.directory)
        initial = store.load()
        self.assertEqual(initial['revision'], 0)
        saved = store.save(initial, expected_revision=0)
        self.assertEqual(saved['revision'], 1)
        self.assertEqual(store.load(), saved)
        self.assertEqual(store.read_snapshot(), saved)
        with self.assertRaises(StoreError) as context:
            store.save(initial, expected_revision=0)
        self.assertEqual(context.exception.code, 'stale-revision')
        self.assertEqual(store.load(), saved)

    def test_native_store_does_not_normalize_ambiguous_path_before_validation(self):
        path = str(self.directory) + '\\.'
        with self.assertRaises(StoreError):
            WindowsProviderStore(path).load()
        self.assertFalse((self.directory / T.LOCK_NAME).exists())

    def test_native_store_does_not_turn_relative_input_into_absolute_authority(self):
        previous = os.getcwd()
        try:
            os.chdir(self.directory)
            with self.assertRaises(StoreError):
                WindowsProviderStore('.').load()
        finally:
            os.chdir(previous)
        self.assertFalse((self.directory / T.LOCK_NAME).exists())


def peer_main():
    directory, mode, kind = sys.argv[2:]
    try:
        if mode == 'save':
            store = WindowsProviderStore(directory)
            candidate = store.load()
            candidate['revision'] = 0
            saved = store.save(candidate, expected_revision=0)
            print(json.dumps({'saved': saved['revision']}))
            return
        with T.private_directory_transaction(directory, exclusive=kind == 'exclusive', timeout=0.2):
            if mode == 'hold':
                (Path(directory) / 'peer-ready.json').write_text(json.dumps({'pid': os.getpid()}))
                sys.stdin.readline()
        print(json.dumps({'acquired': True}))
    except (W.WindowsCustodyError, StoreError) as error:
        print(json.dumps({'error': error.code}))


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--peer':
        peer_main()
    else:
        unittest.main(verbosity=2)
