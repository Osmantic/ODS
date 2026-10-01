"""Owner resolution releases retry without inventing a historical outcome."""
import json
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from project_jobs import ProjectJobs, ProjectRecoveryRequired
from project_owner_recovery import reconcile, quiescence, exclusive_service


@unittest.skipUnless(os.name == 'posix', 'POSIX service custody')
class OwnerRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name) / 'workspace'
        (self.workspace / 'project').mkdir(parents=True)
        self.root = Path(self.temp.name) / 'state'
        self.jobs = ProjectJobs(self.root)
        lock = self.root / 'service.lock'
        lock.touch(mode=0o600)
        self.request = {'project': 'project', 'sourceSha256': 'b' * 64,
                        'image': 'sha256:' + 'a' * 64, 'outputDirectory': 'out'}
        self.job, _ = self.jobs.create('c' * 64, self.request)
        self.jobs.claim(self.job)
        self.jobs.controller_failure(self.job, 'old opaque error')
        self.original = self.jobs.original_receipt(self.job)
        self.digest = self.jobs.receipt_hash(self.original)
        self.evidence = {'engineId': 'test-engine-id', 'engineBootTime': self.original['updated'] + 10,
                         'currentResourcesAbsent': True}
        service = patch('project_owner_recovery.require_stopped_service')
        service.start()
        self.addCleanup(service.stop)
        self.resolutions = {}
        writer = patch('project_owner_resolution.write_resolution', side_effect=lambda job, value: self.resolutions.setdefault((job, value['receipt_hash']), value))
        reader = patch('project_owner_resolution.read_resolution', side_effect=lambda job, root, digest: self.resolutions.get((job, digest)))
        for mock in (writer, reader):
            mock.start()
            self.addCleanup(mock.stop)

    def run_recovery(self, confirm=lambda _: True):
        with patch('project_owner_recovery.quiescence', return_value=self.evidence):
            return reconcile(self.root, self.workspace, self.request['image'], self.job, self.digest, confirm, 'f' * 64)

    def assert_blocked(self):
        with self.assertRaises(ProjectRecoveryRequired):
            self.jobs.create('d' * 64, self.request)

    def test_resolution_preserves_receipt_and_requires_new_request(self):
        self.assert_blocked()
        result = self.run_recovery()
        self.assertEqual(result['historicalOutcome'], 'unknown')
        self.assertEqual(self.jobs.original_receipt(self.job), self.original)
        self.assertEqual(self.jobs.create('c' * 64, self.request), (self.job, False))
        new_job, created = self.jobs.create('d' * 64, self.request)
        self.assertTrue(created)
        self.assertNotEqual(new_job, self.job)
        self.assertEqual(self.jobs.observe(new_job)['state'], 'queued')

    def test_owner_decline_keeps_fence(self):
        with self.assertRaises(PermissionError):
            self.run_recovery(lambda _: False)
        self.assert_blocked()

    def test_service_not_stopped_keeps_fence(self):
        with patch('project_owner_recovery.require_stopped_service', side_effect=ValueError('active')):
            with self.assertRaises(ValueError):
                self.run_recovery()
        self.assert_blocked()

    def test_changed_original_recloses_fence(self):
        self.run_recovery()
        self.jobs.cleanup_warnings(self.job, ['later evidence'])
        self.assert_blocked()

    def test_changed_receipt_requires_fresh_attestation_and_keeps_prior_resolution(self):
        self.run_recovery()
        original_hash = self.digest
        self.jobs.cleanup_warnings(self.job, ['new evidence'])
        self.assert_blocked()
        self.digest = self.jobs.receipt_hash(self.jobs.original_receipt(self.job))
        self.run_recovery()
        self.assertIn((self.job, original_hash), self.resolutions)
        self.assertIn((self.job, self.digest), self.resolutions)
        self.assertTrue(self.jobs.create('d' * 64, self.request)[1])

    def test_tampered_resolution_recloses_fence(self):
        self.run_recovery()
        self.resolutions[(self.job, self.digest)]['record'] = '{}'
        self.assert_blocked()

    def test_stale_hash_denied_before_engine_probe(self):
        self.digest = '0' * 64
        with self.assertRaises(ValueError), patch('project_owner_recovery.command') as command:
            self.run_recovery()
        command.assert_not_called()
        self.assert_blocked()

    def test_live_controller_lock_prevents_resolution(self):
        with exclusive_service(self.root), self.assertRaises(BlockingIOError):
            self.run_recovery()
        self.assert_blocked()

    def test_reservation_or_import_is_not_removed(self):
        state = self.root / 'storage.json'
        state.write_text(json.dumps({self.job: 1024 ** 3}))
        state.chmod(0o600)
        with self.assertRaises(ValueError):
            self.run_recovery()
        self.assertTrue(state.exists())
        state.unlink()
        generation = self.workspace / 'project' / 'ods-builds' / self.job.removeprefix('ods-project-')
        generation.mkdir(parents=True)
        with self.assertRaises(ValueError):
            self.run_recovery()
        self.assertTrue(generation.exists())
        self.assert_blocked()

    def test_engine_change_during_review_is_rejected(self):
        with patch('project_owner_recovery.quiescence', side_effect=[self.evidence, {**self.evidence, 'engineId': 'changed'}]):
            with self.assertRaises(ValueError):
                reconcile(self.root, self.workspace, self.request['image'], self.job, self.digest, lambda _: True, 'f' * 64)
        self.assert_blocked()

    def test_receipt_change_during_review_is_rejected(self):
        def confirm(_):
            self.jobs.cleanup_warnings(self.job, ['changed while reviewed'])
            return True
        with self.assertRaises(ValueError):
            self.run_recovery(confirm)
        self.assert_blocked()

    def test_real_quiescence_command_contract_and_fail_closed_cases(self):
        def engine(args):
            if args[0] == 'info':
                return 'test-engine-id'
            if args[0] == 'run':
                self.assertIn('--read-only', args)
                self.assertEqual(args[-3:], ['/bin/cat', self.request['image'], '/proc/stat'])
                return 'cpu 1 2 3\nbtime 200\n'
            return ''
        with patch('project_owner_recovery.command', side_effect=engine):
            self.assertEqual(quiescence(self.job, self.request['image'], 100)['engineBootTime'], 200)
            with self.assertRaises(ValueError):
                quiescence(self.job, self.request['image'], 300)
        for kind in ('container', 'volume'):
            with self.subTest(kind=kind):
                def occupied(args):
                    return 'foreign-or-owned-resource' if args[0] == kind else engine(args)
                with patch('project_owner_recovery.command', side_effect=occupied), self.assertRaises(ValueError):
                    quiescence(self.job, self.request['image'], 100)
        with patch('project_owner_recovery.command', side_effect=OSError('offline')), self.assertRaises(OSError):
            quiescence(self.job, self.request['image'], 100)


@unittest.skipUnless(os.name == 'posix' and os.getuid() == 0, 'requires isolated real root custody test')
class RootResolutionCustodyTests(unittest.TestCase):
    def test_protected_process_issues_as_root_then_restores_owner(self):
        import project_owner_resolution as receipts
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'receipts'
            job = 'ods-project-' + 'e' * 24
            child = os.fork()
            if child == 0:
                try:
                    receipts.ROOT = root
                    os.setgroups([])
                    os.setresuid(65534, 65534, 0)
                    receipts.write_resolution(job, {'receipt_hash': 'f' * 64, 'record': '{}'})
                    os._exit(0 if os.geteuid() == 65534 else 1)
                except BaseException:
                    os._exit(2)
            _, status = os.waitpid(child, 0)
            self.assertEqual(os.waitstatus_to_exitcode(status), 0)
            self.assertEqual((root / (job + '-' + 'f' * 64 + '.json')).stat().st_uid, 0)

    def test_root_publication_custody_binding_and_no_clobber(self):
        import project_owner_resolution as receipts
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / 'config.json'
            config.write_text(json.dumps({'ownerUid': 0, 'workspace': '/workspace', 'imageId': 'old'}))
            config.chmod(0o644)
            with patch.object(receipts, 'ROOT', root / 'receipts'), patch.object(receipts, 'CONFIG', config):
                identity, _ = receipts.installed_identity()
                job = 'ods-project-' + 'a' * 24
                record = {'ownerUid': 0, 'stateRootSha256': hashlib.sha256(b'/private-state').hexdigest(),
                          'installedConfigSha256': identity,
                          'installationSha256': receipts.installation_identity({'ownerUid': 0, 'workspace': '/workspace'}, '/private-state')}
                value = {'record': json.dumps(record), 'receipt_hash': 'a' * 64, 'record_hash': 'b' * 64}
                receipts.write_resolution(job, value)
                path = receipts.ROOT / (job + '-' + 'a' * 64 + '.json')
                self.assertEqual(path.stat().st_uid, 0)
                self.assertEqual(path.stat().st_nlink, 1)
                self.assertEqual(receipts.read_resolution(job, '/private-state', 'a' * 64), value)
                self.assertIsNone(receipts.read_resolution(job, '/other-installation', 'a' * 64))
                config.write_text(json.dumps({'ownerUid': 0, 'workspace': '/workspace', 'imageId': 'new'}))
                self.assertEqual(receipts.read_resolution(job, '/private-state', 'a' * 64), value)
                config.write_text(json.dumps({'ownerUid': 0, 'workspace': '/other-workspace', 'imageId': 'new'}))
                self.assertIsNone(receipts.read_resolution(job, '/private-state', 'a' * 64))
                config.write_text(json.dumps({'ownerUid': 0, 'workspace': '/workspace', 'imageId': 'new'}))
                before = path.read_bytes()
                with self.assertRaises(FileExistsError):
                    receipts.write_resolution(job, {**value, 'record': 'different'})
                self.assertEqual(path.read_bytes(), before)
                self.assertFalse(list(receipts.ROOT.glob('.pending-*')))
                os.chown(path, 65534, 65534)
                self.assertIsNone(receipts.read_resolution(job, '/private-state', 'a' * 64))
                os.chown(path, 0, 0)
                path.chmod(0o666)
                self.assertIsNone(receipts.read_resolution(job, '/private-state', 'a' * 64))

    def test_same_uid_model_cannot_issue_root_receipt(self):
        # Actual privilege drop in a disposable child, not a mocked UID check.
        child = os.fork()
        if child == 0:
            try:
                os.setgroups([])
                os.setgid(65534)
                os.setuid(65534)
                from project_owner_resolution import write_resolution
                try:
                    write_resolution('ods-project-' + 'b' * 24, {})
                except PermissionError:
                    os._exit(0)
                os._exit(1)
            except BaseException:
                os._exit(2)
        _, status = os.waitpid(child, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 0)


if __name__ == '__main__':
    unittest.main()
