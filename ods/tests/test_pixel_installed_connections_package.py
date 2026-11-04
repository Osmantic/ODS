"""Import the actual Linux installer's copied programs in a fresh isolated Python.

The program copy/writer is executed from the installer body into a disposable
Root-owned directory. No host service, configuration, account or provider is
touched. This qualifies installed import closure, not deployment parent custody.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / 'installers/lib/pixel-host-install.sh'
pytestmark = pytest.mark.skipif(sys.platform != 'linux' or os.geteuid() != 0,
    reason='disposable real Root-owned Linux program copy')


def copied_package(tmp_path):
    text = INSTALLER.read_text(encoding='utf-8')
    start = text.index('def write(path, content, mode, uid=0, gid=0):')
    stop = text.index("\nconfig_dir = pathlib.Path('/etc/ods')", start)
    target = tmp_path / 'protected-program'
    target.mkdir(mode=0o755)
    namespace = {'__name__': 'installed_copy_fixture', 'target': target, 'code_source': ROOT,
        'completed_source_upgrade': None, 'source_upgrade': None,
        'pathlib': __import__('pathlib'), 'stat': stat, 'os': os,
        'tempfile': tempfile, 'hashlib': hashlib}
    exec(compile(text[start:stop], '<actual-linux-program-copy>', 'exec'), namespace)
    return target


def import_installed(target):
    code = '''
import importlib.abc,json,pathlib,sys
root=pathlib.Path(sys.argv[1])
class RefuseMacDependency(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.startswith(('pixel_macos_', 'pixel_connections.native_executor')):
            raise ImportError('Mac dependency reached Linux installed path: '+fullname)
sys.meta_path.insert(0,RefuseMacDependency())
sys.path.insert(0,str(root))
from pixel_connections import coordinator,source_backend,status
assert pathlib.Path(coordinator.__file__).parent==root/'pixel_connections'
assert pathlib.Path(source_backend.__file__).parent==root/'pixel_connections'
assert pathlib.Path(status.__file__).parent==root/'pixel_connections'
calls=[]
coordinator.RUN=lambda argv,timeout:calls.append(argv) or 0
source=coordinator._source_backend(None)
assert type(source) is source_backend.LinuxSource
source.sync()
assert calls==[['systemctl','start','--no-block','pixel-source-broker.service']]
assert not any(n.startswith('pixel_macos_') for n in sys.modules)
print(json.dumps({'installedClosure':True,'linuxBackend':True,'macDependenciesImported':False,'hostCommandsExecuted':False}))
'''
    return subprocess.run([sys.executable, '-I', '-B', '-c', code, str(target)],
        cwd='/', capture_output=True, text=True, timeout=45,
        env={'PATH': '/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1'})


def test_actual_linux_program_copy_has_importable_shared_connection_backend(tmp_path):
    target = copied_package(tmp_path)
    for file in target.rglob('*.py'):
        info = file.lstat()
        assert stat.S_ISREG(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o644
    assert not (target / 'pixel_macos_custody.py').exists()
    result = import_installed(target)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'installedClosure': True, 'linuxBackend': True,
        'macDependenciesImported': False, 'hostCommandsExecuted': False}


def test_missing_installed_backend_cannot_fall_back_to_checkout_or_test_imports(tmp_path):
    target = copied_package(tmp_path)
    backend = target / 'pixel_connections/source_backend.py'
    assert backend.is_file()
    backend.unlink()
    result = import_installed(target)
    assert result.returncode != 0 and 'source_backend' in result.stderr
    assert result.stdout == ''
