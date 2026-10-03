"""Actual native Windows custody tests; synthetic bytes and retained fixtures."""
import csv
import ctypes as C
from contextlib import contextmanager
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
from pixel_provider import windows_custody as W


@unittest.skipUnless(os.name == 'nt', 'Requires actual native Windows APIs')
class NativeCustody(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix='ods-windows-custody-'))
        print('Retained synthetic fixture root:', cls.root, flush=True)
        identity = subprocess.run(['whoami.exe', '/user', '/fo', 'csv', '/nh'],
                                  text=True, capture_output=True, check=True, timeout=10)
        cls.sid = next(csv.reader(io.StringIO(identity.stdout)))[1]
        if not cls.sid.startswith('S-1-5-'):
            raise AssertionError('Unexpected owner identity')
        cls.api = W._api()
        cls.convert = cls.api.a.ConvertStringSecurityDescriptorToSecurityDescriptorW
        cls.convert.argtypes = [C.c_wchar_p, W.U32, C.POINTER(W.PTR), C.POINTER(W.U32)]
        cls.convert.restype = W.BOOL
        cls.mkdir = cls.api.k.CreateDirectoryW
        cls.mkdir.argtypes, cls.mkdir.restype = [C.c_wchar_p, W.PTR], W.BOOL
        cls.write = cls.api.k.WriteFile
        cls.write.argtypes = [W.PTR, W.PTR, W.U32, C.POINTER(W.U32), W.PTR]
        cls.write.restype = W.BOOL
        cls.set_security = cls.api.a.SetFileSecurityW
        cls.set_security.argtypes, cls.set_security.restype = [C.c_wchar_p, W.U32, W.PTR], W.BOOL
        cls.count_handles = cls.api.k.GetProcessHandleCount
        cls.count_handles.argtypes = [W.PTR, C.POINTER(W.U32)]
        cls.count_handles.restype = W.BOOL

    @contextmanager
    def descriptor(self, acl=None):
        descriptor, size = W.PTR(), W.U32()
        sddl = f'O:{self.sid}' + (acl if acl is not None else f'D:P(A;;FA;;;{self.sid})')
        self.assertTrue(self.convert(sddl, 1, C.byref(descriptor), C.byref(size)))
        try:
            yield descriptor
        finally:
            self.api.LocalFree(descriptor)

    def create(self, path, *, directory=False, content=b'fictional fixture', acl=None):
        class Attributes(C.Structure):
            _fields_ = [('length', W.U32), ('descriptor', W.PTR), ('inherit', W.BOOL)]
        with self.descriptor(acl) as descriptor:
            attributes = Attributes(C.sizeof(Attributes), descriptor, False)
            if directory:
                self.assertTrue(self.mkdir(str(path), C.byref(attributes)), C.get_last_error())
                return path
            handle = self.api.CreateFileW(str(path), 0x40000000, 0, C.byref(attributes), 1, 0x80, None)
            self.assertNotIn(handle, (None, W.INVALID_HANDLE), C.get_last_error())
            try:
                count = W.U32()
                buffer = C.create_string_buffer(content)
                self.assertTrue(self.write(handle, buffer, len(content), C.byref(count), None))
                self.assertEqual(count.value, len(content))
            finally:
                self.api.CloseHandle(handle)
        return path

    def setUp(self):
        self.directory = self.create(self.root / uuid.uuid4().hex, directory=True)
        self.file = self.create(self.directory / 'private.txt')

    def change_acl(self, path, acl):
        with self.descriptor(acl) as descriptor:
            self.assertTrue(self.set_security(str(path), 4 | 0x80000000, descriptor), C.get_last_error())

    def assertDenied(self, path, *, directory=False):
        with self.assertRaises(W.WindowsCustodyError):
            with W.open_private(path, directory=directory):
                self.fail('Unsafe target admitted')

    def test_private_read_and_directory(self):
        self.assertEqual(W.read_private(self.file), b'fictional fixture')
        with W.open_private(self.directory, directory=True) as handle:
            volume, index = W.inspect_private(handle, directory=True)
            self.assertIsInstance(volume, int)
            self.assertGreater(index, 0)

    def test_unicode_path(self):
        path = self.create(self.directory / 'café-猫.txt', content=b'unicode name')
        self.assertEqual(W.read_private(path), b'unicode name')

    def test_default_acl_rejected(self):
        self.assertDenied(self.root, directory=True)
        path = self.root / 'inherited.txt'
        path.write_bytes(b'non-secret inherited fixture')
        self.assertDenied(path)

    def test_everyone_acl_rejected(self):
        self.change_acl(self.file, f'D:P(A;;FA;;;{self.sid})(A;;FR;;;WD)')
        self.assertDenied(self.file)

    def test_private_file_requires_private_parent(self):
        path = self.create(self.root / ('private-in-inherited-' + uuid.uuid4().hex))
        self.assertDenied(path)

    def test_empty_acl_rejected(self):
        self.change_acl(self.file, 'D:P')
        self.assertDenied(self.file)

    def test_null_acl_rejected(self):
        self.change_acl(self.file, 'D:NO_ACCESS_CONTROL')
        self.assertDenied(self.file)

    def test_acl_drift_detected_on_same_handle(self):
        with self.assertRaises(W.WindowsCustodyError):
            with W.open_private(self.file) as handle:
                self.change_acl(self.file, f'D:P(A;;FA;;;{self.sid})(A;;FR;;;WD)')
                W.inspect_private(handle)

    def test_parent_acl_drift_rejected_on_exit(self):
        with self.assertRaises(W.WindowsCustodyError):
            with W.open_private(self.file):
                self.change_acl(self.directory, f'D:P(A;;FA;;;{self.sid})(A;;FR;;;WD)')

    def test_impersonation_rejected_without_elevation(self):
        duplicate = self.api.a.DuplicateToken
        duplicate.argtypes, duplicate.restype = [W.PTR, W.U32, C.POINTER(W.PTR)], W.BOOL
        set_thread = self.api.a.SetThreadToken
        set_thread.argtypes, set_thread.restype = [W.PTR, W.PTR], W.BOOL
        revert = self.api.a.RevertToSelf
        revert.argtypes, revert.restype = [], W.BOOL
        token, impersonation = W.PTR(), W.PTR()
        self.assertTrue(self.api.OpenProcessToken(self.api.GetCurrentProcess(), 10, C.byref(token)))
        try:
            self.assertTrue(duplicate(token, 2, C.byref(impersonation)), C.get_last_error())
            self.assertTrue(set_thread(None, impersonation), C.get_last_error())
            try:
                self.assertDenied(self.file)
                with self.assertRaises(W.WindowsCustodyError) as context:
                    with W.open_private(self.directory / 'absent', directory=True, missing_ok=True):
                        self.fail('Missing directory bypassed impersonation refusal')
                self.assertEqual(context.exception.code, 'impersonation-not-supported')
            finally:
                self.assertTrue(revert(), C.get_last_error())
        finally:
            if impersonation:
                self.api.CloseHandle(impersonation)
            self.api.CloseHandle(token)
        self.assertEqual(W.read_private(self.file), b'fictional fixture')

    def test_hardlink_rejected(self):
        link = self.directory / 'hardlink.txt'
        os.link(self.file, link)
        self.assertDenied(self.file)
        self.assertDenied(link)

    def test_reparse_parent_and_target_rejected(self):
        junction = self.root / ('junction-' + uuid.uuid4().hex)
        result = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(junction), str(self.directory)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertDenied(junction, directory=True)
        self.assertDenied(junction / self.file.name)

    def test_path_forms_rejected(self):
        for path in ('relative.txt', '..\\escape', 'C:relative', r'\\server\share\secret',
                     r'\\?\C:\secret', r'\\.\pipe\name', 'C:\\a\\..\\b',
                     'C:\\a\\.\\b', 'C:\\a\\\\b', 'C:\\a\\b.',
                     'C:\\a\\b ', 'C:\\a\\file:stream', 'C:\\a\\NUL.txt',
                     'C:\\a\\CON .txt',
                     'C:\\a\\COM¹', 'C:\\a\\x\x00', 'C:\\a\\x*'):
            with self.subTest(path=repr(path)):
                self.assertDenied(path)

    def test_kind_mismatch_rejected(self):
        self.assertDenied(self.file, directory=True)
        self.assertDenied(self.directory)

    def test_read_bounds(self):
        self.assertEqual(W.read_private(self.file, max_bytes=17), b'fictional fixture')
        with self.assertRaises(W.WindowsCustodyError) as context:
            W.read_private(self.file, max_bytes=16)
        self.assertEqual(context.exception.code, 'file-too-large')
        for value in (0, -1, True, 1048577, '64'):
            with self.assertRaises(W.WindowsCustodyError):
                W.read_private(self.file, max_bytes=value)

    def test_empty_file(self):
        path = self.create(self.directory / 'empty.txt', content=b'')
        self.assertEqual(W.read_private(path, max_bytes=1), b'')

    def test_final_and_ancestor_rename_denied(self):
        with W.open_private(self.file):
            for original in (self.file, self.directory, self.root):
                with self.assertRaises(OSError):
                    os.rename(original, original.with_name(original.name + '-renamed'))
        # Closing the custody context must release the file/ancestor handles.
        renamed = self.file.with_name('renamed.txt')
        os.rename(self.file, renamed)
        self.assertEqual(W.read_private(renamed), b'fictional fixture')

    def test_failed_opens_do_not_leak_handles(self):
        before, after = W.U32(), W.U32()
        self.assertTrue(self.count_handles(self.api.GetCurrentProcess(), C.byref(before)))
        for _ in range(40):
            self.assertDenied(self.root, directory=True)
            self.assertDenied(self.directory / 'missing.txt')
        self.assertTrue(self.count_handles(self.api.GetCurrentProcess(), C.byref(after)))
        self.assertLessEqual(after.value - before.value, 2)

    def test_empty_directory_handle_itself_denies_rename(self):
        empty = self.create(self.directory / 'empty', directory=True)
        with W.open_private(empty, directory=True):
            with self.assertRaises(OSError):
                os.rename(empty, self.directory / 'renamed-empty')

    def test_optional_missing_directory_pins_ancestors_without_creating_state(self):
        missing = self.directory / 'missing'
        with W.open_private(missing, directory=True, missing_ok=True) as handle:
            self.assertIsNone(handle)
            self.assertFalse(missing.exists())
            for ancestor in (self.directory, self.root):
                with self.assertRaises(OSError):
                    os.rename(ancestor, ancestor.with_name(ancestor.name + '-moved'))
        self.assertFalse(missing.exists())
        with self.assertRaises(W.WindowsCustodyError):
            with W.open_private(missing / 'also-missing', directory=True, missing_ok=True):
                self.fail('Missing ancestor treated as a pristine root')

    def test_optional_missing_directory_does_not_admit_existing_unsafe_objects(self):
        for path in (self.root, self.file):
            with self.assertRaises(W.WindowsCustodyError):
                with W.open_private(path, directory=True, missing_ok=True):
                    self.fail('Unsafe existing target treated as missing')
        with W.open_private(self.directory, directory=True, missing_ok=True) as handle:
            self.assertIsInstance(handle, int)
            W.inspect_private(handle, directory=True)

    def test_optional_directory_arguments_are_strict(self):
        for directory, missing_ok in ((False, True), (True, 1), (True, None), (1, True)):
            with self.assertRaises(W.WindowsCustodyError) as context:
                with W.open_private(self.directory, directory=directory, missing_ok=missing_ok):
                    self.fail('Invalid optional directory arguments accepted')
            self.assertEqual(context.exception.code, 'invalid-request')

    def test_path_budget_uses_utf16_units_and_rejects_unpaired_surrogates(self):
        for path in ('C:\\' + '\U0001f331' * 120, 'C:\\invalid\ud800'):
            with self.assertRaises(W.WindowsCustodyError) as context:
                W._path(path)
            self.assertEqual(context.exception.code, 'invalid-path')


@unittest.skipIf(os.name == 'nt', 'Non-Windows import/fail-closed contract')
class UnsupportedPlatform(unittest.TestCase):
    def test_import_is_safe_and_operations_refuse(self):
        with self.assertRaises(W.WindowsCustodyError) as context:
            W.read_private('/tmp/not-opened')
        self.assertEqual(context.exception.code, 'unsupported-platform')
        with self.assertRaises(W.WindowsCustodyError):
            W.inspect_private(123)
        with self.assertRaises(W.WindowsCustodyError):
            with W.open_private('/tmp/not-opened'):
                self.fail('Non-Windows operation admitted')


if __name__ == '__main__':
    unittest.main(verbosity=2)
