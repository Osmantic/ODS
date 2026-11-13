"""Run the actual installer Python in a temporary relocated /usr tree.

Root directory metadata and fchown are simulated; input reads, no-follow flags,
directory creation, file writes and atomic replacement run for real. This is
component coverage, not installed root-custody acceptance.
"""
import ast
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "installers/lib/pixel-host-install.sh"
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX installer fixtures")


def embedded_source():
    text = SOURCE.read_text()
    begin = text.index("_ods_pixel_install_source_broker_kit() {")
    end = text.index("\n_ods_pixel_install_source_broker_if_connected() {", begin)
    helper = text[begin:end]
    return helper.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]


UNIT_NAMES = (
    'pixel-source-broker.service', 'pixel-source-broker.timer',
    'pixel-source-action@.service', 'pixel-source-reconcile@.service',
    'pixel-source-direct.service', 'pixel-source-direct.path',
    'pixel-source-exchange@.service', 'pixel-source-revoke.service',
)
ALL_NAMES = set(UNIT_NAMES) | {'broker.py', 'source-broker.env'}


def _relocate(tree, tmp_path):
    prefix = str(tmp_path / 'usr')
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if value == '/usr':
                node.value = prefix
            elif value.startswith('/usr/'):
                node.value = prefix + value[len('/usr'):]
    return tree


def _compile(tmp_path):
    tree = ast.parse(embedded_source())
    _relocate(tree, tmp_path)
    ast.fix_missing_locations(tree)
    return compile(tree, '<embedded>', 'exec')


def _fake_stat(info, uid):
    values = list(info)
    values[4] = uid
    return os.stat_result(values)


@pytest.fixture
def env(tmp_path, monkeypatch):
    usr = tmp_path / 'usr'
    (usr / 'local').mkdir(parents=True)
    os.chmod(usr, 0o755)
    os.chmod(usr / 'local', 0o755)
    real_lstat = Path.lstat

    def lstat(self):
        info = real_lstat(self)
        text = str(self)
        if stat.S_ISDIR(info.st_mode) and (text == str(usr) or text.startswith(str(usr) + os.sep)):
            return _fake_stat(info, 0)
        return info

    monkeypatch.setattr(Path, 'lstat', lstat)
    def chown(_fd, uid, gid):
        assert (uid, gid) == (0, 0)
    monkeypatch.setattr(os, 'fchown', chown)

    source_root = tmp_path / 'source'
    (source_root / 'deploy' / 'source-broker').mkdir(parents=True)
    (source_root / '.generated').mkdir()
    (source_root / 'deploy' / 'source-broker' / 'broker.py').write_bytes(b'broker\n')
    (source_root / '.generated' / 'source-broker.env').write_bytes(b'env\n')
    for name in UNIT_NAMES:
        (source_root / '.generated' / name).write_bytes(('unit ' + name + '\n').encode())
    return {'tmp': tmp_path, 'usr': usr, 'source': source_root}


def _run(env):
    code = _compile(env['tmp'])
    old = sys.argv
    sys.argv = ['-', str(env['source']), str(os.getuid())]
    try:
        exec(code, {'__name__': '__main__'})
    finally:
        sys.argv = old


def _kit(env):
    return env['usr'] / 'local' / 'libexec' / 'ods-pixel-access' / 'pixel_connections' / 'source-broker-kit'


def _assert_kit(env, libexec_mode=0o755):
    kit = _kit(env)
    assert sorted(p.name for p in kit.iterdir()) == sorted(ALL_NAMES)
    for name in ALL_NAMES:
        info = (kit / name).stat()
        assert stat.S_ISREG(info.st_mode)
        assert stat.S_IMODE(info.st_mode) == 0o644
        original = (env['source'] / 'deploy/source-broker/broker.py' if name == 'broker.py'
                    else env['source'] / '.generated' / name)
        assert (kit / name).read_bytes() == original.read_bytes()
    assert stat.S_IMODE(kit.parent.parent.parent.stat().st_mode) == libexec_mode
    for directory in (kit, kit.parent, kit.parent.parent):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o755


def test_fresh_and_idempotent(env):
    _run(env)
    _assert_kit(env)
    _run(env)
    _assert_kit(env)


def test_restrictive_umask(env):
    old = os.umask(0o077)
    try:
        _run(env)
    finally:
        os.umask(old)
    assert stat.S_IMODE(_kit(env).parent.parent.parent.stat().st_mode) == 0o755
    _assert_kit(env)


def test_existing_libexec_mode_preserved(env):
    libexec = env['usr'] / 'local' / 'libexec'
    libexec.mkdir(mode=0o750)
    os.chmod(libexec, 0o750)
    _run(env)
    assert stat.S_IMODE(libexec.stat().st_mode) == 0o750
    _assert_kit(env, libexec_mode=0o750)


def _expect_fail(env):
    with pytest.raises((SystemExit, OSError)):
        _run(env)


def test_unsafe_libexec_symlink(env):
    target = env['tmp'] / 'outside'
    target.mkdir()
    (env['usr'] / 'local' / 'libexec').symlink_to(target)
    _expect_fail(env)
    assert list(target.iterdir()) == []


def test_foreign_uid(env):
    libexec = env['usr'] / 'local' / 'libexec'
    libexec.mkdir()
    os.chmod(libexec, 0o755)
    real = Path.lstat
    def lstat(self):
        info = real(self)
        if str(self) == str(libexec):
            return _fake_stat(info, 100001)
        return info
    Path.lstat = lstat
    try:
        _expect_fail(env)
    finally:
        Path.lstat = real
    assert list(libexec.iterdir()) == []


def test_writable_libexec(env):
    libexec = env['usr'] / 'local' / 'libexec'
    libexec.mkdir()
    os.chmod(libexec, 0o777)
    _expect_fail(env)
    assert list(libexec.iterdir()) == []


def test_libexec_is_file(env):
    (env['usr'] / 'local' / 'libexec').write_bytes(b'x')
    _expect_fail(env)


def test_usr_local_symlink(env):
    target = env['tmp'] / 'outside'
    target.mkdir()
    local = env['usr'] / 'local'
    local.rmdir()
    local.symlink_to(target)
    _expect_fail(env)
    assert list(target.iterdir()) == []


def test_usr_local_writable(env):
    os.chmod(env['usr'] / 'local', 0o777)
    _expect_fail(env)


def test_unexpected_existing_file(env):
    kit = _kit(env)
    kit.mkdir(parents=True)
    (kit / 'extra').write_bytes(b'x')
    _expect_fail(env)
    assert (kit / 'extra').read_bytes() == b'x'


def test_source_symlink_refused(env):
    target = env['tmp'] / 'real-broker'
    target.write_bytes(b'broker\n')
    broker = env['source'] / 'deploy' / 'source-broker' / 'broker.py'
    broker.unlink()
    broker.symlink_to(target)
    _expect_fail(env)
    assert not _kit(env).exists()
    assert target.read_bytes() == b'broker\n'
