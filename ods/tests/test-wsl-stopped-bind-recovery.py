"""Stopped Desktop recovery must have positive mount and ownership evidence."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/wsl-bind-recovery.py'
spec = importlib.util.spec_from_file_location('stopped_recovery', SCRIPT)
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)


class StoppedRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'source.js'
        self.source.write_text('fixture')
        self.proxy = '/run/desktop/mnt/host/wsl/docker-desktop-bind-mounts/Ubuntu/' + 'a' * 64
        self.target = '/app/fixture.js'
        self.binds = [(str(self.source), self.target, True)]
        self.container = {
            'State': {'Status': 'exited', 'Error': self.error()},
            'Mounts': [{'Type': 'bind', 'Source': self.proxy, 'Destination': self.target, 'RW': False}],
            'Config': {'Labels': {'com.docker.compose.project': 'fixture',
                                  'com.docker.compose.service': 'app',
                                  'com.docker.compose.project.working_dir': str(self.root)}},
        }

    def error(self, source=None, target=None):
        return (f'OCI runtime create failed: error mounting "{source or self.proxy}" '
                f'to rootfs at "{target or self.target}": mount: no such file or directory')

    def test_exact_error_still_requires_deleted_file_proof(self):
        for proof in (False, True):
            with self.subTest(proof=proof), mock.patch.object(H, 'deleted_desktop_file_bind', return_value=proof) as predicate:
                self.assertEqual(H.classify_stopped(self.container, self.binds)[0], proof)
                predicate.assert_called_once_with(str(self.source), self.proxy)

    def test_unrelated_mount_exec_partial_target_and_writeable_bind_refused(self):
        cases = [self.error(source=self.proxy + 'b'), self.error(target=self.target + '.other'),
                 self.error(target='/elsewhere'), f'exec: "{self.target}": no such file or directory']
        with mock.patch.object(H, 'deleted_desktop_file_bind', return_value=True) as predicate:
            for error in cases:
                self.container['State']['Error'] = error
                self.assertFalse(H.classify_stopped(self.container, self.binds)[0])
            self.container['State']['Error'] = self.error()
            self.container['Mounts'][0]['RW'] = True
            self.assertFalse(H.classify_stopped(self.container, self.binds)[0])
            self.assertFalse(H.classify_stopped(self.container, [(str(self.source), self.target, False)])[0])
            predicate.assert_not_called()

    def test_missing_host_source_is_not_repaired(self):
        self.source.unlink()
        with self.assertRaisesRegex(RuntimeError, 'missing host source'):
            H.classify_stopped(self.container, self.binds)

    def test_desktop_inspection_can_retain_the_native_declared_source(self):
        self.container['Mounts'][0]['Source'] = str(self.source)
        with mock.patch.object(H, 'deleted_desktop_file_bind', return_value=True):
            self.assertTrue(H.classify_stopped(self.container, self.binds)[0])
            self.container['Mounts'][0]['Source'] = '/foreign'
            self.assertFalse(H.classify_stopped(self.container, self.binds)[0])

    def test_invalid_proxy_and_symlink_source_refused(self):
        self.assertFalse(H.deleted_desktop_file_bind(str(self.source), '/tmp/elsewhere'))
        self.assertFalse(H.deleted_desktop_file_bind(str(self.source), self.proxy + '/nested'))

    def run_recovery(self, read_only=True, guarded=True, check=False):
        config = {'name': 'fixture', 'services': {'app': {'volumes': [
            {'type': 'bind', 'source': str(self.source), 'target': self.target, 'read_only': read_only}]}}}
        with mock.patch.object(H, 'is_wsl_docker_desktop', return_value=True), \
             mock.patch.object(H, 'compose_config_json', return_value=config), \
             mock.patch.object(H, 'compose_ps', return_value=[{'Service': 'app', 'Name': 'fixture-app'}]), \
             mock.patch.object(H, 'inspect_container', return_value=self.container), \
             mock.patch.object(H, 'deleted_desktop_file_bind', return_value=True), \
             mock.patch.object(H, 'recreate') as recreate, \
             mock.patch.object(H, 'verify', return_value=(True, 'ok')), \
             mock.patch.object(H, 'backup_phantom') as backup:
            args = ['--install-dir', str(self.root), '--service', 'app']
            if guarded:
                args.append('--repair-stopped')
            if check:
                args.append('--check')
            rc = H.main([*args, '--', '-f', 'fixture.yml'])
            backup.assert_not_called()
            return rc, recreate.call_count

    def test_stopped_recovery_requires_root_custody_and_positive_evidence(self):
        self.assertEqual(self.run_recovery(), (0, 1))
        self.container['State']['Error'] = ''
        self.assertEqual(self.run_recovery(), (3, 0))
        self.container['State']['Error'] = self.error()
        self.container['State']['Status'] = 'running'
        self.assertEqual(self.run_recovery(), (1, 0))
        self.container['State']['Status'] = 'exited'
        self.container['Config']['Labels']['com.docker.compose.project.working_dir'] = '/foreign'
        self.assertEqual(self.run_recovery(), (1, 0))

    def test_stopped_recovery_refuses_unobserved_writable_bind_data(self):
        self.assertEqual(self.run_recovery(read_only=False), (1, 0))

    def test_ordinary_postfailure_check_and_repair_keep_stopped_guards(self):
        self.assertEqual(self.run_recovery(guarded=False, check=True), (2, 0))
        self.assertEqual(self.run_recovery(guarded=False), (0, 1))
        self.container['Config']['Labels']['com.docker.compose.project.working_dir'] = '/foreign'
        self.assertEqual(self.run_recovery(guarded=False, check=True), (1, 0))
        self.assertEqual(self.run_recovery(guarded=False), (1, 0))
        self.container['Config']['Labels']['com.docker.compose.project.working_dir'] = str(self.root)
        self.container['State']['Error'] = f'error mounting "{self.proxy}" to rootfs at "{self.target}": not a directory'
        self.assertEqual(self.run_recovery(read_only=False, guarded=False, check=True), (1, 0))
        self.assertEqual(self.run_recovery(read_only=False, guarded=False), (1, 0))

    def test_running_stale_foreign_root_is_not_recreated(self):
        self.container['State']['Status'] = 'running'
        self.container['Config']['Labels']['com.docker.compose.project.working_dir'] = '/foreign'
        with mock.patch.object(H, 'classify_running', return_value=(True, 'stale-bind:/app/fixture.js')):
            self.assertEqual(self.run_recovery(guarded=False, check=True), (1, 0))
            self.assertEqual(self.run_recovery(guarded=False), (1, 0))


if __name__ == '__main__':
    unittest.main()
