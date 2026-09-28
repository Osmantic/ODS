"""Ownership/ordering tests; no system services or WSL distributions are changed."""
import importlib.util
import io
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
            if name == 'pixel-preview-inspection.service':
                self.contents[self.root / 'extensions/services/pixel-agent/host' / name] = data
        self.patches = [patch.object(module, 'regular', side_effect=lambda p,*a,**kw:self.contents[p]),
                        patch.object(module.os, 'getuid', return_value=1000, create=True),
                        patch.object(module.os.path, 'lexists', side_effect=lambda p: Path(p) in self.contents),
                        patch.object(Path, 'exists', return_value=True)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_only_exact_pixel_units(self):
        result = module.managed_units(self.root, self.home)
        self.assertEqual(result, list(module.NATIVE_UNITS))
        self.assertNotIn('pixel-ops-broker.service', result)
        self.assertNotIn('docker.service', result)

    def test_legacy_inspection_absence_is_distinct_from_partial_install(self):
        self.contents.pop(Path('/etc/systemd/system/pixel-preview-inspection.service'))
        self.assertNotIn('pixel-preview-inspection.service', module.managed_units(self.root, self.home))
        self.contents[Path('/etc/ods-pixel-inspection.json')] = b'{}'
        with self.assertRaisesRegex(RuntimeError, 'Incomplete preview inspection'):
            module.managed_units(self.root, self.home)

    def test_inspection_source_drift_is_rejected(self):
        self.contents[Path('/etc/systemd/system/pixel-preview-inspection.service')] += b'changed'
        with self.assertRaisesRegex(RuntimeError, 'Inspection unit differs'):
            module.managed_units(self.root, self.home)

    def test_portal_gateway_keeps_exact_owner_contract(self):
        path = Path('/etc/systemd/system/openclaw-gateway.service')
        self.contents[path] = self.contents[path].replace(b'Gateway - Pixel', b'Gateway - Portal')
        self.assertEqual(module.managed_units(self.root, self.home), list(module.NATIVE_UNITS))
        self.contents[path] = self.contents[path].replace(str(self.root).encode(), b'/home/another/ods')
        with self.assertRaisesRegex(RuntimeError, 'Gateway unit'):
            module.managed_units(self.root, self.home)

    def test_gateway_description_must_be_an_exact_known_line(self):
        path = Path('/etc/systemd/system/openclaw-gateway.service')
        for name in (b'Pixel Personal', b'Portal Personal'):
            self.contents[path] = (b'Description=OpenClaw Gateway - ' + name +
                b'\nBindReadOnlyPaths=' + str(self.root).encode() + b'/extensions/services/pixel-agent/plugin\n')
            with self.assertRaisesRegex(RuntimeError, 'Gateway unit'):
                module.managed_units(self.root, self.home)

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

    def run_adapter(self, action, failure=None, active='inactive', restart_agent=True):
        calls = []
        def invoke(args, **kwargs):
            calls.append((args, kwargs))
            if failure and failure in args:
                raise subprocess.CalledProcessError(1, args)
            return subprocess.CompletedProcess(args, 0, stdout=active+'\n')
        with patch.object(module, 'regular'), patch.object(Path, 'resolve', return_value=self.root), \
             patch.object(Path, 'is_symlink', return_value=False), \
             patch.object(module, 'managed_units', return_value=list(module.NATIVE_UNITS)), \
             patch.object(module, 'host_agent_restart', return_value=restart_agent) as admission, \
             patch.object(module.subprocess, 'run', side_effect=invoke):
            result = module.run(action, self.root)
            if action.endswith('-start'):
                admission.assert_called_once_with(self.root)
            else:
                admission.assert_not_called()
        return result, calls

    def test_plan_binds_owner_and_root_without_commands(self):
        result, calls = self.run_adapter('plan-stop')
        self.assertEqual(result, {'schemaVersion':1,'action':'stop','installRoot':str(self.root),
                                 'ownerUid':1000,'nativeUnits':list(module.NATIVE_UNITS),
                                 'hostAgentRestart':False})
        self.assertEqual(calls, [])

    def test_plan_start_always_restarts_an_owned_agent(self):
        for installed in (True, False):
            with self.subTest(installed=installed):
                result, calls = self.run_adapter('plan-start', restart_agent=installed)
                self.assertIs(result['hostAgentRestart'], installed)
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
        self.assertEqual(calls[0][0],['bash',str(self.root/'ods-cli'),'start',
                                    '--defer-wsl-agent-restart'])
        self.assertNotIn('systemctl', calls[0][0])

    def test_agent_admission_failure_prevents_compose(self):
        with patch.object(module, 'regular'), patch.object(Path, 'resolve', return_value=self.root), \
             patch.object(Path, 'is_symlink', return_value=False), \
             patch.object(module, 'managed_units', return_value=[]), \
             patch.object(module, 'host_agent_restart', side_effect=RuntimeError('foreign agent')), \
             patch.object(module.subprocess, 'run') as invoke:
            for action in ('plan-start', 'compose-start'):
                with self.subTest(action=action), self.assertRaisesRegex(RuntimeError, 'foreign agent'):
                    module.run(action, self.root)
            invoke.assert_not_called()

    def test_root_execution_is_rejected(self):
        with patch.object(module.os,'getuid',return_value=0):
            with self.assertRaisesRegex(RuntimeError,'ordinary installation owner'):
                self.run_adapter('plan-start')

    def test_invalid_action_rejected(self):
        with self.assertRaises(ValueError):
            self.run_adapter('purge')

    def test_entrypoint_distinguishes_timeout_from_retryable_failure(self):
        for failure, expected in ((subprocess.TimeoutExpired('bash', 300), 124),
                                  (subprocess.CalledProcessError(1, 'bash'), 1)):
            with self.subTest(code=expected), patch.object(module, 'run', side_effect=failure), \
                    patch.object(module.sys, 'argv', ['wsl_stack.py', 'compose-start', str(self.root)]), \
                    patch.object(module.sys, 'stderr', io.StringIO()):
                self.assertEqual(module.main(), expected)


class HostAgentOwnership(unittest.TestCase):
    def setUp(self):
        self.root = Path('/home/owner/ods')
        self.unit = Path('/etc/systemd/system/ods-host-agent.service')
        self.values = {'LoadState': 'loaded', 'FragmentPath': str(self.unit),
                       'User': 'owner', 'DropInPaths': '',
                       'ExecStart': self.exec_start()}
        self.custody = patch.object(module, 'regular', return_value=b'[Service]\n').start()
        self.addCleanup(patch.stopall)
        patch.object(module, 'owner_name', return_value='owner').start()
        patch.object(module.os, 'getuid', return_value=1000, create=True).start()

    def exec_start(self, interpreter='/usr/bin/python3', arguments=' --require-ods-network'):
        return (f'{{ path={interpreter} ; argv[]={interpreter} '
                f'{self.root / "bin/ods-host-agent.py"}{arguments} ; ignore_errors=no ; '
                'start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }')

    def probe(self, code=0, values=None):
        data = self.values if values is None else values
        output = '\n'.join(f'{name}={value}' for name, value in data.items()) + '\n'
        with patch.object(module.subprocess, 'run', return_value=subprocess.CompletedProcess(
                ['/usr/bin/systemctl'], code, stdout=output, stderr='')) as invoke:
            result = module.host_agent_restart(self.root)
        invoke.assert_called_once_with(
            ['/usr/bin/systemctl', 'show', 'ods-host-agent.service',
             '--property=LoadState,FragmentPath,User,ExecStart,DropInPaths'],
            capture_output=True, text=True, check=False, timeout=10)
        return result

    def test_exact_owned_agent_all_supported_interpreters_and_flags(self):
        for interpreter in ('/usr/bin/python3', '/usr/local/bin/python3', '/usr/bin/python3.12'):
            for arguments in ('', ' --require-ods-network'):
                with self.subTest(interpreter=interpreter, arguments=arguments):
                    self.values['ExecStart'] = self.exec_start(interpreter, arguments)
                    self.assertTrue(self.probe())
        self.custody.assert_called_with(self.unit, 0)

    def test_absent_legacy_agent_needs_no_restart_or_custody(self):
        for code in (0, 1, 4):
            with self.subTest(code=code):
                self.assertFalse(self.probe(code, {'LoadState': 'not-found'}))
        self.custody.assert_not_called()

    def test_absent_agent_with_metadata_is_rejected(self):
        self.values['LoadState'] = 'not-found'
        with self.assertRaisesRegex(RuntimeError, 'unexpected ownership metadata'):
            self.probe(4)

    def test_foreign_root_owner_path_dropins_and_bad_executables_rejected(self):
        changes = (
            ('FragmentPath', str(self.unit.with_name('another.service'))),
            ('FragmentPath', '/usr/lib/systemd/system/ods-host-agent.service'),
            ('DropInPaths', '/etc/systemd/system/ods-host-agent.service.d/override.conf'),
            ('User', 'another'),
            ('ExecStart', self.exec_start().replace(str(self.root), '/home/another/ods')),
            ('ExecStart', self.exec_start('/home/owner/python3')),
            ('ExecStart', self.exec_start(arguments=' --other-flag')),
            ('ExecStart', self.exec_start() + ' ' + self.exec_start()),
            ('ExecStart', self.exec_start().replace('ignore_errors=no', 'ignore_errors=yes')),
            ('LoadState', 'masked'),
        )
        for name, value in changes:
            original = self.values[name]
            with self.subTest(property=name, value=value), self.assertRaises(RuntimeError):
                self.values[name] = value
                self.probe()
            self.values[name] = original

    def test_unprotected_fragment_and_read_errors_propagate(self):
        for failure in (RuntimeError('unsafe unit'), PermissionError('denied'), OSError('I/O')):
            with self.subTest(error=type(failure).__name__):
                self.custody.side_effect = failure
                with self.assertRaises(type(failure)):
                    self.probe()

    def test_systemctl_failure_is_not_an_absent_service_fallback(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.probe(1)
        with self.assertRaises(subprocess.CalledProcessError):
            self.probe(2, {'LoadState': 'not-found'})

    def test_malformed_missing_duplicate_properties_fail_closed(self):
        outputs = ('garbage', 'LoadState=loaded\n',
                   'LoadState=not-found\nLoadState=not-found\n')
        for output in outputs:
            with self.subTest(output=output), patch.object(module.subprocess, 'run', return_value=
                    subprocess.CompletedProcess([], 0, stdout=output)) as invoke:
                with self.assertRaises(RuntimeError):
                    module.host_agent_restart(self.root)
                self.assertEqual(invoke.call_count, 1)

    def test_probe_timeout_does_not_run_a_fallback_or_root_command(self):
        with patch.object(module.subprocess, 'run', side_effect=
                          subprocess.TimeoutExpired('systemctl', 10)) as invoke:
            with self.assertRaises(subprocess.TimeoutExpired):
                module.host_agent_restart(self.root)
            self.assertEqual(invoke.call_count, 1)
            self.assertEqual(invoke.call_args.args[0][:3],
                             ['/usr/bin/systemctl', 'show', 'ods-host-agent.service'])


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
