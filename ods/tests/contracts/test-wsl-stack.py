"""Ownership/ordering tests; no system services or WSL distributions are changed."""
import importlib.util
import json
import os
import tempfile
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[2] / 'installers/lib/wsl_stack.py'
spec = importlib.util.spec_from_file_location('wsl_stack', SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class StackContract(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[2]
        self.home = self.root.parent / 'wsl-test-owner'
        self.marker = {'schema_version': 2, 'manager': 'ods', 'state': 'ready',
                       'install_dir': str(self.root), 'initial_active_state': 'absent'}
        self.contents = {}
        self.contents[self.home / '.config/ods/pixel-managed.json'] = json.dumps(self.marker).encode()
        for name in module.NATIVE_UNITS:
            data = ('[Service]\n' + name + '\n').encode()
            if name == 'openclaw-gateway.service':
                data = f'Description=OpenClaw Gateway - Pixel\nBindReadOnlyPaths={self.root}/extensions/services/pixel-agent/plugin\n'.encode()
            elif name == 'pixel-ingress.service':
                data = b'Description=Pixel Agent host ingress\nExecStart=/usr/bin/env node /usr/local/libexec/ods-pixel-ingress.mjs\nEnvironmentFile=/etc/ods/pixel-agent.env\n'
            self.contents[Path('/etc/systemd/system') / name] = data
            self.contents[self.root / 'data/pixel' / name.removeprefix('pixel-')] = data
        self.patches = [patch.object(module, 'regular', side_effect=lambda p,*a,**kw:self.contents[p]),
                        patch.object(module.os, 'getuid', return_value=1000, create=True),
                        patch.object(Path, 'exists', return_value=True)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_only_exact_pixel_units(self):
        result = module.managed_units(self.root, self.home)
        self.assertEqual(result, list(module.NATIVE_UNITS))
        self.assertNotIn('pixel-ops-broker.service', result)
        self.assertNotIn('docker.service', result)

    def test_foreign_install_root_rejected(self):
        self.marker['install_dir'] = '/home/another/ods'
        self.contents[self.home / '.config/ods/pixel-managed.json'] = json.dumps(self.marker).encode()
        with self.assertRaisesRegex(RuntimeError, 'ownership marker'):
            module.managed_units(self.root, self.home)

    def test_incomplete_install_rejected(self):
        self.marker['state'] = 'installing'
        self.contents[self.home / '.config/ods/pixel-managed.json'] = json.dumps(self.marker).encode()
        with self.assertRaises(RuntimeError):
            module.managed_units(self.root, self.home)

    def test_foreign_gateway_rejected(self):
        self.contents[Path('/etc/systemd/system/openclaw-gateway.service')] = b'Description=Personal Gateway\n'
        with self.assertRaisesRegex(RuntimeError, 'Gateway unit'):
            module.managed_units(self.root, self.home)

    def test_drifted_auxiliary_rejected(self):
        self.contents[Path('/etc/systemd/system/pixel-workspace-preview.service')] = b'another install'
        with self.assertRaisesRegex(RuntimeError, 'differs'):
            module.managed_units(self.root, self.home)

    def run_adapter(self, action, failure=None, active='inactive'):
        calls = []
        def invoke(args, **kwargs):
            calls.append((args, kwargs))
            if failure and failure in args:
                raise subprocess.CalledProcessError(1, args)
            return subprocess.CompletedProcess(args, 0, stdout=active+'\n')
        with patch.object(module, 'regular'), patch.object(Path, 'resolve', return_value=self.root), \
             patch.object(Path, 'is_symlink', return_value=False), \
             patch.object(module, 'managed_units', return_value=list(module.NATIVE_UNITS)), \
             patch.object(module.subprocess, 'run', side_effect=invoke):
            result = module.run(action, self.root)
        return result, calls

    def test_plan_binds_owner_and_root_without_commands(self):
        result, calls = self.run_adapter('plan-stop')
        self.assertEqual(result, {'schemaVersion':1,'action':'stop','installRoot':str(self.root),
                                 'ownerUid':1000,'nativeUnits':list(module.NATIVE_UNITS)})
        self.assertEqual(calls, [])

    def test_compose_runs_only_owner_cli_with_pinned_root(self):
        result, calls = self.run_adapter('compose-stop')
        self.assertEqual(result['state'],'stopped')
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][0], ['bash',str(self.root/'ods-cli'),'stop'])
        self.assertEqual(calls[0][1]['env']['INSTALL_DIR'], str(self.root))
        self.assertEqual(calls[0][1]['cwd'], self.root)
        self.assertNotIn('sudo', calls[0][0])

    def test_start_uses_same_owner_route(self):
        _, calls = self.run_adapter('compose-start')
        self.assertEqual(calls[0][0],['bash',str(self.root/'ods-cli'),'start'])
        self.assertNotIn('systemctl', calls[0][0])

    def test_root_execution_is_rejected(self):
        with patch.object(module.os,'getuid',return_value=0):
            with self.assertRaisesRegex(RuntimeError,'ordinary installation owner'):
                self.run_adapter('plan-start')

    def test_invalid_action_rejected(self):
        with self.assertRaises(ValueError):
            self.run_adapter('purge')


@unittest.skipUnless(os.name == 'posix', 'Linux file custody checks require POSIX descriptors')
class RealFileCustody(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'record'
        self.path.write_bytes(b'bounded original')
        self.path.chmod(0o600)

    def test_private_regular_file(self):
        self.assertEqual(module.regular(self.path,os.getuid(),private=True), b'bounded original')

    def test_symlink_rejected(self):
        link = self.path.with_name('link')
        link.symlink_to(self.path)
        with self.assertRaises(OSError):
            module.regular(link,os.getuid())

    def test_hardlink_rejected(self):
        os.link(self.path,self.path.with_name('hardlink'))
        with self.assertRaises(RuntimeError):
            module.regular(self.path,os.getuid())

    def test_size_and_mode_limits(self):
        with self.assertRaises(RuntimeError):
            module.regular(self.path,os.getuid(),maximum=2)
        self.path.chmod(0o644)
        with self.assertRaises(RuntimeError):
            module.regular(self.path,os.getuid(),private=True)

    def test_fifo_does_not_block(self):
        fifo=self.path.with_name('fifo')
        os.mkfifo(fifo)
        with self.assertRaises(RuntimeError):
            module.regular(fifo,os.getuid())


if __name__ == '__main__':
    unittest.main()
