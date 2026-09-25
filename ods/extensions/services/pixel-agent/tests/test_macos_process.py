import ctypes
import errno
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / 'bin'))
import pixel_macos_process as process


class ProcessIdentityTests(unittest.TestCase):
    def setUp(self):
        self.record = (123, 1700000000, 456, 501, 20, 501, 20, 501, 20)
        self.library = Mock()
        self.path = b'/fixture/node'
        def pidpath(pid, buffer, capacity):
            self.assertEqual(pid, 123)
            self.assertEqual(capacity, 4096)
            ctypes.memmove(buffer, self.path + b'\0', len(self.path) + 1)
            return len(self.path)
        self.library.proc_pidpath.side_effect = pidpath
        library = patch.object(process, '_library', return_value=self.library)
        record = patch.object(process, '_record', return_value=self.record)
        self.lib = library.start()
        self.read = record.start()
        self.addCleanup(library.stop)
        self.addCleanup(record.stop)

    def identity(self, **kwargs):
        return process.process_identity(123, **{'uid':501, 'gid':20,
                                               'executable':'/fixture/node', **kwargs})

    def test_stable_identity_reads_process_twice(self):
        self.assertEqual(self.identity(), (*self.record, '/fixture/node'))
        self.assertEqual(self.read.call_count, 2)

    def test_root_requires_explicit_opt_in_and_still_checks_credentials(self):
        with self.assertRaisesRegex(ValueError, 'specification-invalid'):
            self.identity(uid=0, gid=0)
        root = (123, 1700000000, 456, 0, 0, 0, 0, 0, 0)
        self.read.return_value = root
        self.assertEqual(self.identity(uid=0, gid=0, allow_root=True), (*root, '/fixture/node'))
        self.read.return_value = self.record
        with self.assertRaisesRegex(ValueError, 'owner-mismatch'):
            self.identity(uid=0, gid=0, allow_root=True)
        with self.assertRaisesRegex(ValueError, 'specification-invalid'):
            self.identity(uid=0, gid=0, allow_root=1)

    def test_reused_pid_or_changed_credentials_fail(self):
        for field in range(1, 9):
            changed = list(self.record)
            changed[field] += 1
            self.read.side_effect = [self.record, tuple(changed)]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'process-changed'):
                self.identity()

    def test_effective_real_and_saved_credentials_are_required(self):
        for field in range(3, 9):
            changed = list(self.record)
            changed[field] += 1
            self.read.return_value = tuple(changed)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'owner-mismatch'):
                self.identity()

    def test_exact_path_and_bounded_terminated_api_reply_required(self):
        for path in (b'/other/node', b'/fixture/node\0extra'):
            self.path = path
            with self.assertRaisesRegex(ValueError, 'executable-mismatch'):
                self.identity()
        self.library.proc_pidpath.side_effect = None
        for size in (0, -1, 4096, 4097):
            self.library.proc_pidpath.return_value = size
            with self.assertRaisesRegex(ValueError, 'path-unavailable'):
                self.identity()

    def test_invalid_specifications_do_not_query_processes(self):
        for specification in ({'uid':0}, {'uid':True}, {'gid':-1}, {'gid':True},
                              {'executable':'relative'}, {'executable':'/a/../b'},
                              {'executable':'/a//b'}, {'executable':'/a\0b'}):
            with self.subTest(specification=specification), self.assertRaises(ValueError):
                self.identity(**specification)
        for pid in (0, -1, True, 2147483648):
            with self.assertRaises(ValueError):
                process.process_identity(pid, uid=501, gid=20, executable='/fixture/node')
        self.lib.assert_not_called()


class NativeRecordTests(unittest.TestCase):
    def test_failed_record_preserves_kernel_errno_without_stale_values(self):
        for code in (errno.ESRCH, errno.EPERM, errno.EIO):
            def failed(*_args):
                ctypes.set_errno(code)
                return 0
            with self.assertRaises(process.ProcessIdentityError) as raised:
                process._record(Mock(proc_pidinfo=failed), 123)
            self.assertEqual(raised.exception.errno, code)
        ctypes.set_errno(errno.ESRCH)
        with self.assertRaises(process.ProcessIdentityError) as raised:
            process._record(Mock(proc_pidinfo=Mock(return_value=0)), 123)
        self.assertEqual(raised.exception.errno, 0)

    def test_public_bsd_structure_and_short_read_fail_closed(self):
        self.assertEqual(ctypes.sizeof(process._BsdInfo), 136)
        self.assertEqual(process._BsdInfo.start_sec.offset, 120)
        for size in (0, -1, 135, 137):
            with self.assertRaisesRegex(ValueError, 'unavailable'):
                process._record(Mock(proc_pidinfo=Mock(return_value=size)), 123)

    def test_kernel_record_must_identify_live_requested_process(self):
        def read(info):
            def fill(pid, flavor, arg, destination, size):
                self.assertEqual((pid, flavor, arg, size), (123, 3, 0, 136))
                ctypes.memmove(destination, ctypes.byref(info), size)
                return size
            return process._record(Mock(proc_pidinfo=fill), 123)
        baseline = {'pid':123, 'status':2, 'start_sec':1700000000, 'start_usec':999999}
        self.assertEqual(read(process._BsdInfo(**baseline))[:3], (123, 1700000000, 999999))
        for changed in ({'pid':456}, {'status':0}, {'status':5}, {'start_sec':0}, {'start_usec':1000000}):
            with self.assertRaisesRegex(ValueError, 'mismatch'):
                read(process._BsdInfo(**{**baseline, **changed}))

    def test_other_platform_cannot_load_native_api(self):
        with patch.object(process.sys, 'platform', 'linux'), patch.object(process.ctypes, 'CDLL') as load:
            with self.assertRaisesRegex(ValueError, 'platform-required'):
                process._library()
            load.assert_not_called()

    @unittest.skipUnless(sys.platform == 'darwin' and os.getuid() > 0, 'native unprivileged macOS required')
    def test_real_child_identity_and_exit(self):
        child = subprocess.Popen(['/bin/sleep', '30'])
        try:
            first = process.process_identity(child.pid, uid=os.getuid(), gid=os.getgid(), executable='/bin/sleep')
            self.assertEqual(first, process.process_identity(child.pid, uid=os.getuid(), gid=os.getgid(), executable='/bin/sleep'))
            self.assertEqual(first[0], child.pid)
            with self.assertRaisesRegex(ValueError, 'executable-mismatch'):
                process.process_identity(child.pid, uid=os.getuid(), gid=os.getgid(), executable='/bin/sh')
            with self.assertRaisesRegex(ValueError, 'owner-mismatch'):
                process.process_identity(child.pid, uid=os.getuid()+1, gid=os.getgid(), executable='/bin/sleep')
        finally:
            child.terminate()
            child.wait(timeout=5)
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            process.process_identity(child.pid, uid=os.getuid(), gid=os.getgid(), executable='/bin/sleep')


if __name__ == '__main__':
    unittest.main()
