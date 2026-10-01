"""Exercise the installer's public-directory blocks with a restrictive umask."""
import ast
import os
from pathlib import Path
import stat
from types import SimpleNamespace
from unittest.mock import patch

import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'installers/lib/pixel-host-install.sh'


def directory_code():
    text = SOURCE.read_text().split('_ods_pixel_install_access_service() {', 1)[1]
    body = text.split("<<'PY'\n", 1)[1].split('\nPY\n', 1)[0]
    tree = ast.parse(body)
    blocks = []
    active = False
    for node in tree.body:
        if isinstance(node, ast.Assign):
            name = getattr(node.targets[0], 'id', '')
            if name in ('target', 'settings_package', 'provider_package'):
                active = True
                if name == 'target':
                    continue  # Supply an isolated fixture root, not /usr/local.
            elif name in ('host', 'config_dir'):
                active = False
        if isinstance(node, ast.FunctionDef):
            active = False
        if active:
            # The program-copy loops are unrelated to directory permissions.
            if isinstance(node, ast.For) and getattr(node.target, 'id', '') == 'name':
                active = False
                continue
            blocks.append(node)
    return compile(ast.fix_missing_locations(ast.Module(body=blocks, type_ignores=[])),
                   str(SOURCE), 'exec')


def run_install(target, *, bad_owner=None):
    real_lstat = Path.lstat
    ancestors = set(target.parents)

    def root_fixture_lstat(path, *args, **kwargs):
        value = real_lstat(path, *args, **kwargs)
        # The test directory lives beneath pytest's private /tmp tree. Model
        # the already-protected system ancestors and root ownership, retaining
        # actual filesystem modes/types for every owned program directory.
        mode = stat.S_IFDIR | 0o755 if path in ancestors else value.st_mode
        return SimpleNamespace(st_mode=mode, st_uid=12345 if path == bad_owner else 0)

    previous = os.umask(0o077)
    try:
        with patch.object(Path, 'lstat', root_fixture_lstat):
            exec(directory_code(), {'target': target, 'os': os, 'stat': stat})
    finally:
        os.umask(previous)


class ProgramModesTests(unittest.TestCase):
    def test_public_directories_are_traversable_under_private_umask(self):
        for existing in (False, True):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                target = tmp_path / 'programs'
                if existing:
                    for path in (target, target / 'pixel_settings', target / 'pixel_provider'):
                        path.mkdir(mode=0o700)
                state = tmp_path / 'private-state'
                state.mkdir(mode=0o700)
                secret = state / 'key'
                secret.write_text('fixture only')
                secret.chmod(0o600)
                run_install(target)
                for path in (target, target / 'pixel_settings', target / 'pixel_provider'):
                    assert stat.S_IMODE(path.stat().st_mode) == 0o755
                assert stat.S_IMODE(state.stat().st_mode) == 0o700
                assert stat.S_IMODE(secret.stat().st_mode) == 0o600

    def test_unsafe_directory_is_rejected_before_chmod(self):
        for component in ('', 'pixel_settings', 'pixel_provider'):
            for unsafe in ('symlink', 'writable', 'owner'):
                with self.subTest(component=component, unsafe=unsafe), tempfile.TemporaryDirectory() as tmp:
                    tmp_path = Path(tmp)
                    target = tmp_path / 'programs'
                    target.mkdir(mode=0o755)
                    path = target / component if component else target
                    if component:
                        path.mkdir(mode=0o700)
                    destination = tmp_path / 'outside'
                    destination.mkdir(mode=0o700)
                    if unsafe == 'symlink':
                        path.rmdir()
                        path.symlink_to(destination, target_is_directory=True)
                    elif unsafe == 'writable':
                        path.chmod(0o775)
                    before = path.lstat().st_mode
                    with self.assertRaisesRegex(SystemExit, 'not root protected'):
                        run_install(target, bad_owner=path if unsafe == 'owner' else None)
                    assert path.lstat().st_mode == before
                    assert stat.S_IMODE(destination.stat().st_mode) == 0o700


if __name__ == '__main__':
    unittest.main()
