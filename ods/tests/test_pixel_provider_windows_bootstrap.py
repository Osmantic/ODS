"""Actual native first-root creation; synthetic state only, retained fixtures."""
import ctypes as C
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import test_pixel_provider_windows_custody as fixtures
from test_pixel_provider_windows_credentials import document
from pixel_provider import windows_bootstrap as B
from pixel_provider import windows_custody as W
from pixel_provider.windows_vault import WindowsCredentialStore


@unittest.skipUnless(os.name == 'nt', 'Requires actual native Windows APIs')
class NativeBootstrap(unittest.TestCase):
    setUpClass = classmethod(fixtures.NativeCustody.setUpClass.__func__)
    setUp = fixtures.NativeCustody.setUp
    descriptor = fixtures.NativeCustody.descriptor
    create = fixtures.NativeCustody.create
    change_acl = fixtures.NativeCustody.change_acl

    def test_create_then_actual_credentials_and_new_process_readback(self):
        target = self.directory / 'providers'
        identity = B.create_private_root(target)
        with W.open_private(target, directory=True) as handle:
            self.assertEqual(W.inspect_private(handle, directory=True), identity)
        self.assertEqual(list(target.iterdir()), [])
        store = WindowsCredentialStore(target)
        saved = store.save_public(dict(document=document(), expectedRevision=0,
            credentialChanges={'tower': {'action': 'set', 'value': 'synthetic-key'}}))
        self.assertEqual(saved['revision'], 1)
        peer = subprocess.run([sys.executable, __file__, '--read', str(target)],
                              capture_output=True, text=True, timeout=8,
                              creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(peer.returncode, 0, peer.stderr)
        self.assertEqual(json.loads(peer.stdout), {'revision': 1, 'syntheticKeyMatched': True})

    def test_existing_private_directory_not_adopted_or_modified(self):
        with W.open_private(self.directory, directory=True) as handle:
            before = W.inspect_private(handle, directory=True)
        with self.assertRaises(W.WindowsCustodyError) as context:
            B.create_private_root(self.directory)
        self.assertEqual(context.exception.code, 'already-exists')
        with W.open_private(self.directory, directory=True) as handle:
            self.assertEqual(W.inspect_private(handle, directory=True), before)
        self.assertEqual(self.file.read_bytes(), b'fictional fixture')
        self.assertEqual([p.name for p in self.directory.iterdir()], ['private.txt'])

    def test_existing_unsafe_directory_not_repaired(self):
        self.change_acl(self.directory, f'D:P(A;;FA;;;{self.sid})(A;;FR;;;WD)')
        with self.assertRaises(W.WindowsCustodyError):
            B.create_private_root(self.directory)
        with self.assertRaises(W.WindowsCustodyError):
            with W.open_private(self.directory, directory=True):
                self.fail('Unsafe ACL was repaired')
        self.assertEqual(self.file.read_bytes(), b'fictional fixture')

    def test_regular_file_collision_preserves_contents(self):
        with self.assertRaises(W.WindowsCustodyError):
            B.create_private_root(self.file)
        self.assertEqual(W.read_private(self.file), b'fictional fixture')

    def test_missing_ancestor_is_not_created(self):
        with self.assertRaises(W.WindowsCustodyError):
            B.create_private_root(self.directory / 'missing' / 'providers')
        self.assertFalse((self.directory / 'missing').exists())

    def test_root_relative_ambiguous_and_device_paths_refuse_before_create(self):
        for target in ('C:\\', 'relative', 'C:relative', r'\\server\share\private',
                       r'\\?\C:\private', r'\\.\pipe\private', str(self.directory) + '\\.\\x',
                       str(self.directory) + '\\..\\x', str(self.directory) + '\\x:stream',
                       str(self.directory) + '\\NUL', str(self.directory) + '\\x.',
                       str(self.directory) + '\\x ', str(self.directory) + '\\bad\ud800'):
            with self.subTest(target=repr(target)), patch.object(B, '_create_api') as create:
                with self.assertRaises(W.WindowsCustodyError):
                    B.create_private_root(target)
                create.assert_not_called()

    def test_path_budget_reserves_transaction_filename_in_utf16_units(self):
        # This path is <=240 Python code points but exceeds the reserved UTF-16 budget.
        target = str(self.directory) + '\\' + '\U0001f331' * 50
        with patch.object(B, '_create_api') as create, self.assertRaises(W.WindowsCustodyError):
            B.create_private_root(target)
        create.assert_not_called()
        self.assertEqual([p.name for p in self.directory.iterdir()], ['private.txt'])

    def test_unicode_leaf_can_hold_actual_provider_transaction(self):
        target = self.directory / 'providers-\U0001f331'
        B.create_private_root(target)
        store = WindowsCredentialStore(target)
        initial = store.load()
        self.assertEqual(store.save(initial, expected_revision=0)['revision'], 1)

    def test_inherited_parent_is_unchanged_and_child_private(self):
        # Actual inherited Windows ACL from mkdtemp; bootstrap never chmods it.
        target = self.root / ('inherited-parent-child-' + self.directory.name)
        before = subprocess.run(['icacls.exe', str(self.root)], capture_output=True,
                                text=True, timeout=10, check=True).stdout
        B.create_private_root(target)
        after = subprocess.run(['icacls.exe', str(self.root)], capture_output=True,
                               text=True, timeout=10, check=True).stdout
        self.assertEqual(before, after)
        with W.open_private(target, directory=True):
            pass

    def test_junction_ancestor_and_final_target_are_never_followed(self):
        junction = self.directory / 'junction'
        other = self.create(self.directory / 'other', directory=True)
        result = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(junction), str(other)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        for path in (junction, junction / 'providers'):
            with self.assertRaises(W.WindowsCustodyError):
                B.create_private_root(path)
        self.assertEqual(list(other.iterdir()), [])

    def test_new_root_and_ancestors_are_pinned_from_create_return(self):
        target = self.directory / 'providers'
        original, observed = B._create_api(), []
        def inspect_creation(*args):
            result = original(*args)
            self.assertEqual(result, 0)
            for path in (target, self.directory, self.root):
                with self.assertRaises(OSError):
                    os.rename(path, path.with_name(path.name + '-moved'))
                observed.append(str(path))
            return result
        with patch.object(B, '_create_api', return_value=inspect_creation):
            B.create_private_root(target)
        self.assertEqual(len(observed), 3)
        os.rename(target, target.with_name('released'))

    def test_real_creation_then_exception_retains_private_directory(self):
        target = self.directory / 'providers'
        original = B._create_api()
        def fail_after(*args):
            self.assertEqual(original(*args), 0)
            raise OSError('synthetic after actual creation')
        with patch.object(B, '_create_api', return_value=fail_after):
            with self.assertRaises(W.WindowsCustodyError) as context:
                B.create_private_root(target)
        self.assertEqual(context.exception.code, 'creation-outcome-unknown')
        with W.open_private(target, directory=True):
            pass
        self.assertEqual(list(target.iterdir()), [])

    def test_postcreate_acl_drift_retained_and_never_reported_ready(self):
        target = self.directory / 'providers'
        original = B._create_api()
        def drift_after(*args):
            result = original(*args)
            self.assertEqual(result, 0)
            self.change_acl(target, f'D:P(A;;FA;;;{self.sid})(A;;FR;;;WD)')
            return result
        with patch.object(B, '_create_api', return_value=drift_after):
            with self.assertRaises(W.WindowsCustodyError) as context:
                B.create_private_root(target)
        self.assertEqual(context.exception.code, 'creation-outcome-unknown')
        self.assertTrue(target.is_dir())
        with self.assertRaises(W.WindowsCustodyError):
            with W.open_private(target, directory=True):
                self.fail('Unsafe state silently repaired')

    def test_actual_two_process_creation_has_one_winner(self):
        target = self.directory / 'providers'
        children = [subprocess.Popen([sys.executable, __file__, '--create', str(target)],
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
                    child.kill()  # Exact owned test child only.
                child.communicate(timeout=5)
        self.assertCountEqual(outcomes, [{'created': True}, {'error': 'already-exists'}])

    def test_repeated_warm_creates_and_collisions_do_not_grow_handle_use(self):
        # A cold first native create observed +8 handles, then flat across20.
        # Its cause is not attributed here; measure repeated-operation growth.
        B.create_private_root(self.directory / 'warmup')
        before, after = W.U32(), W.U32()
        self.assertTrue(self.count_handles(self.api.GetCurrentProcess(), C.byref(before)))
        counts = [before.value]
        for i in range(20):
            B.create_private_root(self.directory / ('new-' + str(i)))
            with self.assertRaises(W.WindowsCustodyError):
                B.create_private_root(self.directory)
            self.assertTrue(self.count_handles(self.api.GetCurrentProcess(), C.byref(after)))
            counts.append(after.value)
        self.assertTrue(self.count_handles(self.api.GetCurrentProcess(), C.byref(after)))
        self.assertLessEqual(after.value - before.value, 2, counts)


@unittest.skipIf(os.name == 'nt', 'Non-Windows import/refusal')
class UnsupportedBootstrap(unittest.TestCase):
    def test_import_safe_operation_refuses(self):
        with self.assertRaises(W.WindowsCustodyError) as context:
            B.create_private_root('/not-created-by-this-test')
        self.assertEqual(context.exception.code, 'unsupported-platform')


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] in ('--create', '--read'):
        try:
            if sys.argv[1] == '--create':
                B.create_private_root(sys.argv[2])
                print(json.dumps({'created': True}))
            else:
                store = WindowsCredentialStore(sys.argv[2])
                value = store.resolve_credential('tower', expected_revision=1)
                print(json.dumps({'revision': store.load()['revision'],
                                  'syntheticKeyMatched': value == 'synthetic-key'}))
        except W.WindowsCustodyError as error:
            print(json.dumps({'error': error.code}))
    else:
        unittest.main()
