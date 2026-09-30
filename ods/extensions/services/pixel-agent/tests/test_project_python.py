"""Real isolated Python lifecycle and protocol boundary regressions."""
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch

HOST = Path(__file__).resolve().parents[1] / "host"
sys.path.insert(0, str(HOST))
from project_controller import ProjectController  # noqa: E402
from project_runtime import stage_arguments, seed_project, run_stage, observe_stage  # noqa: E402
from project_snapshot import snapshot_project  # noqa: E402

IMAGE = "sha256:" + "a" * 64
JOB = "ods-project-" + "a" * 24
LOCK = "humanize==4.12.3 --hash=sha256:2cbf6370af06568fa6d2da77c86edb7886f3160ecd19ee1ffef07979efc597f6\n"


def fixture(root, *, main=None, test=None, lock=LOCK):
    project = Path(root) / "project"
    project.mkdir()
    (project / "tests").mkdir()
    (project / "ods-project.json").write_text('{"runtime":"python"}')
    (project / "requirements.lock").write_text(lock)
    (project / "main.py").write_text(main or "import pathlib, humanize; pathlib.Path('out').mkdir(); pathlib.Path('out/result.txt').write_text(humanize.intcomma(12345))")
    (project / "tests/test_main.py").write_text(test or """import os, pathlib, socket, unittest, humanize
class RealTests(unittest.TestCase):
 def test_package_and_isolation(self):
  self.assertEqual(humanize.intcomma(12345), '12,345')
  self.assertNotEqual(os.getuid(), 0)
  self.assertFalse(pathlib.Path('/var/run/docker.sock').exists())
  self.assertFalse(pathlib.Path('/home/gabs/ods/.env').exists())
  with self.assertRaises(OSError):
   socket.create_connection(('1.1.1.1', 443), timeout=1)
""")
    return project


class PythonStageBoundaryTests(unittest.TestCase):
    def test_trusted_profile_keeps_network_only_for_wheel_download(self):
        for stage in ('acquire', 'test', 'build'):
            args = stage_arguments(IMAGE, JOB, stage, runtime='python')
            self.assertEqual(args[args.index('--network') + 1], 'bridge' if stage == 'acquire' else 'none')
            self.assertEqual(args[args.index('--user') + 1], '1000:1000')
            command = args[args.index(IMAGE) + 1:]
            self.assertIn('-I', command)
            if stage == 'acquire':
                self.assertIn('--require-hashes', command)
                self.assertIn('--no-deps', command)
                self.assertIn('--only-binary=:all:', command)
                self.assertNotIn('main.py', ' '.join(command))
            self.assertIn('--read-only', args)
        with self.assertRaises(ValueError):
            stage_arguments(IMAGE, JOB, 'build', runtime='shell')

    def test_manifest_seed_never_exposes_python_source_to_online_stage(self):
        files = {'ods-project.json': b'{"runtime":"python"}', 'requirements.lock': LOCK.encode(),
                 'main.py': b'import os', 'tests/test_main.py': b'pass',
                 'sitecustomize.py': b'raise RuntimeError("source loaded online")',
                 'pip.py': b'raise RuntimeError("source pip loaded online")'}
        with patch('project_runtime.subprocess.run') as run:
            seed_project(IMAGE, JOB, {'files': files}, manifests_only=True, runtime='python')
        payload = run.call_args.kwargs['input']
        with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
            self.assertEqual(archive.getnames(), ['ods-project.json', 'requirements.lock'])
        with self.assertRaises(ValueError):
            seed_project(IMAGE, JOB, {'files': files}, manifests_only=True)


@unittest.skipUnless(os.name == 'posix', 'POSIX controller')
class PythonControllerBindingTests(unittest.TestCase):
    def test_unconfigured_python_fails_before_execution(self):
        with tempfile.TemporaryDirectory() as root:
            fixture(root)
            controller = ProjectController(root, Path(root) / 'state', IMAGE, authorize=lambda *args: True)
            try:
                with patch('project_controller.subprocess.run') as run:
                    with self.assertRaisesRegex(ValueError, 'not installed'):
                        controller.submit('a' * 64, 'project')
                    run.assert_not_called()
            finally:
                controller.close()

    def test_stale_image_cannot_start_after_configuration_changes(self):
        with tempfile.TemporaryDirectory() as root:
            controller = ProjectController(root, Path(root) / 'state', IMAGE,
                                           authorize=lambda *args: True, python_image='sha256:' + 'b' * 64)
            request = {'project': 'project', 'image': IMAGE, 'runtime': 'python',
                       'sourceSha256': 'a' * 64, 'outputDirectory': 'out'}
            job, _ = controller.jobs.create('a' * 64, request)
            try:
                with patch('project_controller.subprocess.run') as run:
                    controller._work(job, request, {}, threading.Event())
                    run.assert_not_called()
                self.assertEqual(controller.observe(job)['state'], 'unconfirmed')
            finally:
                controller.close()


@unittest.skipUnless(os.environ.get('ODS_TEST_PROJECT_PYTHON') == '1', 'real Docker opt-in')
class PythonDockerRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as root:
            iid = Path(root) / 'image'
            subprocess.run(['docker', 'build', '--iidfile', str(iid), '-f', str(HOST / 'Dockerfile.project-python'), str(HOST)],
                           check=True, capture_output=True, timeout=240)
            cls.image = iid.read_text().strip()

    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.job = 'ods-project-' + uuid.uuid4().hex[:24]

    def tearDown(self):
        for stage in ('seed-manifests', 'seed-source', 'acquire', 'test', 'build'):
            subprocess.run(['docker', 'rm', '-f', self.job + '-' + stage], capture_output=True, timeout=20)
        subprocess.run(['docker', 'volume', 'rm', self.job], capture_output=True, timeout=20)
        self.root.cleanup()

    def prepare(self, **kwargs):
        project = fixture(self.root.name, **kwargs)
        # Hostile Python bootstrap names must not execute during acquisition.
        (project / 'sitecustomize.py').write_text('raise RuntimeError("untrusted startup hook")')
        (project / 'pip.py').write_text('raise RuntimeError("untrusted pip")')
        source = snapshot_project(self.root.name, 'project')
        seed_project(self.image, self.job, source, manifests_only=True, runtime='python')
        result = run_stage(self.image, self.job, 'acquire', cancel=threading.Event(), runtime='python')
        self.assertEqual(result['status'], 'succeeded', result)
        seed_project(self.image, self.job, source, manifests_only=False, runtime='python')
        return project

    def test_full_pipeline_import_and_reopen_do_not_replay(self):
        project = fixture(self.root.name)
        def allowed(project, action, binding):
            return project == 'project'
        controller = ProjectController(self.root.name, Path(self.root.name) / 'state', IMAGE,
                                       authorize=allowed, python_image=self.image)
        try:
            accepted = controller.submit('a' * 64, 'project')
        finally:
            controller.close()
        result = controller.observe(accepted['id'])
        self.assertEqual(result['state'], 'succeeded', result)
        self.assertEqual(result['request']['runtime'], 'python')
        self.assertEqual(result['request']['image'], self.image)
        self.assertEqual([r['stage'] for r in result['steps']], ['acquire', 'test', 'build'])
        self.assertEqual((Path(self.root.name) / result['output']['relativeDirectory'] / 'result.txt').read_text(), '12,345')
        self.assertFalse((project / '.ods-python-env').exists())
        self.assertFalse((project / 'out').exists())
        reopened = ProjectController(self.root.name, Path(self.root.name) / 'state', IMAGE,
                                     authorize=allowed, python_image=self.image)
        try:
            self.assertEqual(reopened.submit('a' * 64, 'project')['id'], result['id'])
            self.assertFalse(reopened.futures)
        finally:
            reopened.close()
        lookup = subprocess.run(['docker', 'volume', 'inspect', accepted['id']], capture_output=True)
        self.assertNotEqual(lookup.returncode, 0)

    def test_bad_hash_fails_before_source_execution(self):
        fixture(self.root.name, lock=LOCK.replace('2cbf', '3cbf'))
        source = snapshot_project(self.root.name, 'project')
        seed_project(self.image, self.job, source, manifests_only=True, runtime='python')
        result = run_stage(self.image, self.job, 'acquire', cancel=threading.Event(), runtime='python')
        self.assertEqual(result['status'], 'failed', result)
        self.assertIn('HASHES', result['stderr'])

    def test_zero_tests_and_failed_tests_cannot_pass(self):
        self.prepare(test='print("deliberately no tests")')
        result = run_stage(self.image, self.job, 'test', cancel=threading.Event(), runtime='python')
        self.assertEqual(result['status'], 'failed', result)
        self.assertIn('ODS discovered tests: 0', result['stdout'])

    def test_early_zero_exit_during_discovery_is_not_a_pass(self):
        self.prepare(test='import os; os._exit(0)')
        result = run_stage(self.image, self.job, 'test', cancel=threading.Event(), runtime='python')
        self.assertEqual(result['status'], 'failed', result)
        self.assertIn('completion receipt', result['stderr'])

    def test_failing_assertion_blocks_build(self):
        self.prepare(test='import unittest\nclass T(unittest.TestCase):\n def test_failure(self): self.fail("deliberate failure")\n')
        result = run_stage(self.image, self.job, 'test', cancel=threading.Event(), runtime='python')
        self.assertEqual(result['status'], 'failed', result)
        self.assertIn('deliberate failure', result['stderr'])

    def test_missing_transitive_dependency_fails_offline_without_resolution(self):
        self.prepare(lock='requests==2.32.5 --hash=sha256:2462f94637a34fd532264295e186976db0f5d453d1cdd31473c85a6a161affb6\n')
        result = run_stage(self.image, self.job, 'test', cancel=threading.Event(), runtime='python')
        self.assertEqual(result['status'], 'failed', result)
        self.assertIn('No matching distribution found', result['stderr'])
        self.assertEqual(observe_stage(self.image, self.job, 'test', runtime='python')['status'], 'failed')

    def test_cancel_build_stops_exact_container(self):
        self.prepare(main='import time; time.sleep(120)')
        tested = run_stage(self.image, self.job, 'test', cancel=threading.Event(), runtime='python')
        self.assertEqual(tested['status'], 'succeeded', tested)
        cancel = threading.Event()
        timer = threading.Timer(2, cancel.set)
        timer.start()
        try:
            result = run_stage(self.image, self.job, 'build', cancel=cancel, runtime='python')
        finally:
            timer.cancel()
        self.assertEqual(result['status'], 'cancelled', result)
        self.assertEqual(observe_stage(self.image, self.job, 'build', runtime='python')['evidence'], 'docker-state')
        self.assertEqual(observe_stage(self.image, self.job, 'build')['evidence'], 'identity-mismatch')


if __name__ == '__main__':
    unittest.main()
