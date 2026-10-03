"""Execute both pinned Python filesystem workers under real mkdir races."""
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
import unittest


PACKAGE = os.environ.get('OPENCLAW_PACKAGE_DIR')
HOST = Path(__file__).parents[1] / 'host'
MODULES = {'bridge': 'browser-bridges-D-At-KLc.js', 'secure': 'secure-temp-dir-XAWcZnE2.js'}


def sources(kind):
    manifest = json.loads((HOST / f'openclaw-sandbox-mkdir-{kind}.json').read_text())
    data = (Path(PACKAGE) / 'dist' / MODULES[kind]).read_bytes()
    if hashlib.sha256(data).hexdigest() == manifest['patchedSha256']:
        for before, after in reversed(manifest['replacements']):
            assert data.count(after.encode()) == 1
            data = data.replace(after.encode(), before.encode())
    assert hashlib.sha256(data).hexdigest() == manifest['sourceSha256']
    patched = data
    for before, after in manifest['replacements']:
        assert patched.count(before.encode()) == 1
        patched = patched.replace(before.encode(), after.encode())
    assert hashlib.sha256(patched).hexdigest() == manifest['patchedSha256']
    return data.decode(), patched.decode()


def worker(kind, source, mkdir):
    if kind == 'bridge':
        lines = source.splitlines()
        start = next(i for i, line in enumerate(lines) if line.strip() == '"DIR_FLAGS = os.O_RDONLY",')
        end = next(i for i in range(start, len(lines)) if lines[i].strip().startswith('"def create_temp_file('))
        code = '\n'.join(json.loads(line.strip().removesuffix(',')) for line in lines[start:end])
    else:
        start = source.index('DIR_FLAGS = os.O_RDONLY')
        code = source[start:source.index('def parent_and_basename(', start)]
    namespace = {'os': SimpleNamespace(**{**vars(os), 'mkdir': mkdir}), 'errno': errno}
    exec(code, namespace)
    return lambda fd, value, create: namespace['walk_dir'](fd, value if kind == 'bridge' else value.split('/'), create)


@unittest.skipUnless(PACKAGE and os.name == 'posix', 'requires the pinned POSIX OpenClaw package')
class SandboxMkdir(unittest.TestCase):
    def test_concurrent_writers_reproduce_failure_and_both_succeed_after_repair(self):
        for kind in MODULES:
            for patched, source in enumerate(sources(kind)):
                with self.subTest(kind=kind, patched=patched), tempfile.TemporaryDirectory() as root:
                    barrier = Barrier(2)

                    def racing_mkdir(name, mode, *, dir_fd):
                        if name == 'shared':
                            barrier.wait(timeout=5)
                        os.mkdir(name, mode, dir_fd=dir_fd)

                    walk = worker(kind, source, racing_mkdir)
                    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        def write_child(name):
                            fd = walk(root_fd, f'shared/{name}', True)
                            try:
                                self.assertTrue(stat.S_ISDIR(os.fstat(fd).st_mode))
                            finally:
                                os.close(fd)

                        with ThreadPoolExecutor(max_workers=2) as pool:
                            futures = [pool.submit(write_child, name) for name in ('one', 'two')]
                            errors = [future.exception() for future in futures if future.exception()]
                        if patched:
                            self.assertEqual(errors, [])
                            self.assertTrue((Path(root) / 'shared/one').is_dir())
                            self.assertTrue((Path(root) / 'shared/two').is_dir())
                        else:
                            self.assertEqual(len(errors), 1)
                            self.assertIsInstance(errors[0], FileExistsError)
                    finally:
                        os.close(root_fd)

    def test_raced_files_and_symlinks_still_fail_and_permissions_are_not_swallowed(self):
        for kind in MODULES:
            for obstacle in ('file', 'symlink', 'permission'):
                with self.subTest(kind=kind, obstacle=obstacle), tempfile.TemporaryDirectory() as root:
                    outside = Path(root) / 'outside'
                    outside.mkdir()

                    def blocked_mkdir(name, mode, *, dir_fd):
                        if obstacle == 'permission':
                            raise PermissionError(errno.EACCES, 'denied')
                        if obstacle == 'symlink':
                            os.symlink(outside, name, dir_fd=dir_fd)
                        else:
                            fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=dir_fd)
                            os.close(fd)
                        os.mkdir(name, mode, dir_fd=dir_fd)

                    walk = worker(kind, sources(kind)[1], blocked_mkdir)
                    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        with self.assertRaises(PermissionError if obstacle == 'permission' else OSError):
                            walk(root_fd, 'raced/child', True)
                        self.assertFalse((outside / 'child').exists())
                        with self.assertRaises(FileNotFoundError):
                            walk(root_fd, 'missing', False)
                        self.assertFalse((Path(root) / 'missing').exists())
                    finally:
                        os.close(root_fd)


if __name__ == '__main__':
    if not PACKAGE:
        raise SystemExit('Set OPENCLAW_PACKAGE_DIR to the pinned OpenClaw package')
    unittest.main()
