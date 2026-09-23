"""Offline checks for dependency locks and the installer's real pip boundary."""
import ast
import hashlib
import pathlib
import subprocess
import sys
import tempfile
import types
import unittest
import zipfile


ROOT = pathlib.Path(__file__).resolve().parents[2]
LOCK_DIRS = (ROOT / '.github/requirements', ROOT / 'ods/installers/python-deps')


class PythonLockTests(unittest.TestCase):
    def test_each_locked_requirement_has_an_exact_version_and_sha256(self):
        for directory in LOCK_DIRS:
            for lock in directory.glob('*.txt'):
                with self.subTest(lock=lock.name):
                    logical = lock.read_text().replace('\\\n', ' ')
                    entries = [line.strip() for line in logical.splitlines()
                               if line.strip() and not line.lstrip().startswith('#')]
                    self.assertTrue(entries)
                    for entry in entries:
                        self.assertRegex(entry, r'^[A-Za-z0-9_.-]+==[A-Za-z0-9_.+!-]+(?:\s|$)')
                        self.assertRegex(entry, r'--hash=sha256:[0-9a-f]{64}(?:\s|$)')
                        self.assertNotIn('://', entry)

    def test_windows_runtime_installer_rejects_substituted_wheel_before_import(self):
        # Run the real Windows helper against an offline fixture wheel. Replace
        # only the interpreter/path/import probes; no agent or service starts.
        agent_file = ROOT / 'ods/bin/ods-host-agent.py'
        tree = ast.parse(agent_file.read_text(encoding='utf-8'))
        helper = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                      and node.name == '_ensure_windows_resolver_pyyaml')
        with tempfile.TemporaryDirectory(prefix='ods-hash-boundary-') as directory:
            stage = pathlib.Path(directory)
            wheels = stage / 'wheels'
            wheels.mkdir()
            wheel = wheels / 'ods_hash_fixture-0.0.0-py3-none-any.whl'
            with zipfile.ZipFile(wheel, 'w') as archive:
                archive.writestr('ods_hash_fixture.py', 'VALUE = "original"\n')
                archive.writestr('ods_hash_fixture-0.0.0.dist-info/METADATA',
                                 'Metadata-Version: 2.1\nName: ods-hash-fixture\nVersion: 0.0.0\n')
                archive.writestr('ods_hash_fixture-0.0.0.dist-info/WHEEL',
                                 'Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
                archive.writestr('ods_hash_fixture-0.0.0.dist-info/RECORD', '')
            approved_hash = hashlib.sha256(wheel.read_bytes()).hexdigest()
            lock = stage / 'installers/python-deps/pyyaml.txt'
            lock.parent.mkdir(parents=True)
            lock.write_text(f'ods-hash-fixture==0.0.0 --hash=sha256:{approved_hash}\n')
            with zipfile.ZipFile(wheel, 'a') as archive:
                archive.writestr('substituted.txt', 'unapproved bytes')
            target = stage / 'packages'

            def offline_pip(command, **kwargs):
                self.assertIn('--require-hashes', command)
                self.assertIn('--only-binary=:all:', command)
                self.assertEqual(pathlib.Path(command[-1]), lock)
                command = [item for item in command if item != '--user']
                return subprocess.run([*command, '--no-index', '--find-links', str(wheels),
                                       '--target', str(target)], **kwargs)

            namespace = dict(Path=pathlib.Path, __file__=str(stage / 'bin/ods-host-agent.py'),
                platform=types.SimpleNamespace(system=lambda: 'Windows'),
                logger=types.SimpleNamespace(warning=lambda *args: None),
                _python_can_import=lambda *args: False, _process_can_import=lambda *args: False,
                subprocess=types.SimpleNamespace(run=offline_pip, SubprocessError=subprocess.SubprocessError))
            exec(compile(ast.Module(body=[helper], type_ignores=[]), str(agent_file), 'exec'), namespace)
            with self.assertRaisesRegex(RuntimeError, 'HASHES'):
                namespace['_ensure_windows_resolver_pyyaml'](sys.executable)
            self.assertFalse((target / 'ods_hash_fixture.py').exists())


if __name__ == '__main__':
    unittest.main()
