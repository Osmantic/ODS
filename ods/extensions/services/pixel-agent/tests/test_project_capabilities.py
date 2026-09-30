"""Identity evidence is measured in the installed image, not the host/workspace."""
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import Mock, patch

HOST = Path(__file__).resolve().parents[1] / 'host'
sys.path.insert(0, str(HOST))
from project_capabilities import (probe_arguments, probe_python_runtime, validate_evidence,  # noqa: E402
                                  cleanup_pending_probe, ProbeCleanupPending, PROBE)
from project_controller import ProjectController  # noqa: E402
from project_dispatch import dispatch_project  # noqa: E402
from project_authority import ManagedFullAccessPolicy  # noqa: E402

IMAGE = 'sha256:' + 'a' * 64
EVIDENCE = {
    'python': {'version': '3.11.14', 'implementation': 'cpython', 'cacheTag': 'cpython-311',
               'soabi': 'cpython-311-aarch64-linux-gnu'},
    'platform': {'os': 'linux', 'machine': 'aarch64', 'libc': {'name': 'glibc', 'version': '2.36'}},
    'wheelCompatibility': {'source': 'pip._vendor.packaging.tags.sys_tags', 'complete': True,
        'tagCount': 3, 'groups': [{'pythonTag': 'cp311', 'abiTag': 'cp311',
                                'platformTags': ['manylinux_2_36_aarch64', 'manylinux_2_17_aarch64']},
                               {'pythonTag': 'py3', 'abiTag': 'none', 'platformTags': ['any']}]},
}


class CapabilityProbeTests(unittest.TestCase):
    def process(self, payload=None, code=0):
        return Mock(stdout=io.BytesIO(payload if payload is not None else json.dumps(EVIDENCE).encode()),
                    stderr=io.BytesIO(b''), wait=Mock(return_value=code))

    def test_probe_has_no_source_network_mounts_or_mutable_image(self):
        args = probe_arguments(IMAGE, 'ods-project-capabilities-' + '1' * 24)
        self.assertEqual(args[args.index('--network') + 1], 'none')
        self.assertEqual(args[args.index('--pull') + 1], 'never')
        self.assertEqual(args[args.index('--workdir') + 1], '/')
        self.assertEqual(args[args.index('--user') + 1], '1000:1000')
        self.assertEqual(args[args.index(IMAGE) + 1:], ['-I', '-c', PROBE])
        self.assertIn('--read-only', args)
        self.assertIn('--rm', args)
        self.assertIn('no-new-privileges', args)
        self.assertNotIn('--mount', args)
        self.assertNotIn('--volume', args)
        self.assertNotIn('--env', args)
        self.assertNotIn('shell', args)
        for image in ('python:latest', '', None, IMAGE + ';anything'):
            with self.assertRaises(ValueError):
                probe_arguments(image, 'ods-project-capabilities-' + '1' * 24)

    def test_probe_uses_image_evidence_and_always_removes_container(self):
        with patch('project_capabilities.subprocess.Popen', return_value=self.process()) as launch, \
                patch('project_capabilities.cleanup_pending_probe') as cleanup:
            self.assertEqual(probe_python_runtime(IMAGE), EVIDENCE)
        args = launch.call_args.args[0]
        cleanup.assert_called_once_with(IMAGE, args[args.index('--name') + 1])

    def test_timeout_kills_client_and_removes_exact_container(self):
        process = self.process()
        process.wait.return_value = 1
        with patch('project_capabilities.subprocess.Popen', return_value=process), \
                patch('project_capabilities.cleanup_pending_probe') as cleanup, \
                patch('project_capabilities.time.monotonic', side_effect=[0, 2]):
            with self.assertRaises(subprocess.TimeoutExpired):
                probe_python_runtime(IMAGE, timeout=1)
        process.kill.assert_called_once()
        cleanup.assert_called_once()

    def test_shutdown_cancels_running_probe_and_removes_container(self):
        cancel = threading.Event()
        process = self.process()
        def wait(**_kwargs):
            cancel.set()
            if process.kill.called:
                return 1
            raise subprocess.TimeoutExpired('docker', 0.1)
        process.wait.side_effect = wait
        with patch('project_capabilities.subprocess.Popen', return_value=process), \
                patch('project_capabilities.cleanup_pending_probe') as cleanup:
            with self.assertRaises(InterruptedError):
                probe_python_runtime(IMAGE, cancel=cancel)
        process.kill.assert_called_once()
        cleanup.assert_called_once()

    def test_oversized_nonzero_and_malformed_output_fail_closed(self):
        for payload, code in ((b'x' * 65537, 0), (b'{}', 0), (b'not json', 0),
                              (b'[' * 2000 + b']' * 2000, 0), (b'{}', 1)):
            with self.subTest(size=len(payload), code=code), \
                    patch('project_capabilities.subprocess.Popen', return_value=self.process(payload, code)), \
                    patch('project_capabilities.cleanup_pending_probe') as cleanup:
                with self.assertRaises(ValueError):
                    probe_python_runtime(IMAGE)
                cleanup.assert_called_once()

    def test_cleanup_failure_does_not_produce_verified_identity(self):
        with patch('project_capabilities.subprocess.Popen', return_value=self.process()), \
                patch('project_capabilities.cleanup_pending_probe', side_effect=subprocess.CalledProcessError(1, 'docker rm')):
            with self.assertRaises(ProbeCleanupPending):
                probe_python_runtime(IMAGE)

    def test_pending_cleanup_requires_positive_absence_or_exact_identity(self):
        name = 'ods-project-capabilities-' + '1' * 24
        cid = 'b' * 64
        expected = ' '.join(json.dumps(item) for item in (IMAGE, '/' + name, name)).encode()
        with patch('project_capabilities.subprocess.run', return_value=Mock(stdout=b'')) as run:
            cleanup_pending_probe(IMAGE, name)
            self.assertEqual(run.call_count, 1)
        with patch('project_capabilities.subprocess.run', side_effect=[Mock(stdout=cid.encode()),
                   Mock(stdout=expected), Mock()]) as run:
            cleanup_pending_probe(IMAGE, name)
            self.assertEqual(run.call_args.args[0], ['docker', 'rm', '-f', cid])
        for identity in (b'other image', b'{}'):
            with patch('project_capabilities.subprocess.run', side_effect=[Mock(stdout=cid.encode()),
                       Mock(stdout=identity)]) as run:
                with self.assertRaises(ValueError):
                    cleanup_pending_probe(IMAGE, name)
                self.assertEqual(run.call_count, 2)
        with patch('project_capabilities.subprocess.run', side_effect=subprocess.CalledProcessError(1, 'docker ls')):
            with self.assertRaises(subprocess.CalledProcessError):
                cleanup_pending_probe(IMAGE, name)

    def test_cleanup_has_one_five_second_deadline_across_all_commands(self):
        name = 'ods-project-capabilities-' + '1' * 24
        expected = ' '.join(json.dumps(item) for item in (IMAGE, '/' + name, name)).encode()
        with patch('project_capabilities.subprocess.run', side_effect=[Mock(stdout=b'b' * 64),
                   Mock(stdout=expected)]) as run, \
                patch('project_capabilities.time.monotonic', side_effect=[0, 1, 3, 5.1]):
            with self.assertRaises(subprocess.TimeoutExpired):
                cleanup_pending_probe(IMAGE, name)
            self.assertEqual(run.call_count, 2)

    def test_cancelled_probe_keeps_cleanup_receipt_when_docker_is_unavailable(self):
        cancel = threading.Event()
        process = self.process()
        def wait(**_kwargs):
            cancel.set()
            if process.kill.called:
                return 1
            raise subprocess.TimeoutExpired('docker', 0.1)
        process.wait.side_effect = wait
        with patch('project_capabilities.subprocess.Popen', return_value=process) as launch, \
                patch('project_capabilities.cleanup_pending_probe', side_effect=OSError('Docker offline')):
            with self.assertRaises(ProbeCleanupPending) as raised:
                probe_python_runtime(IMAGE, cancel=cancel)
        args = launch.call_args.args[0]
        self.assertEqual(raised.exception.image, IMAGE)
        self.assertEqual(raised.exception.name, args[args.index('--name') + 1])

    def test_malformed_partial_duplicate_and_injected_tags_rejected(self):
        mutations = [lambda v: v['wheelCompatibility'].update(complete=False),
                     lambda v: v['wheelCompatibility'].update(tagCount=True),
                     lambda v: v['wheelCompatibility'].update(tagCount=4),
                     lambda v: v['wheelCompatibility']['groups'][0]['platformTags'].append('manylinux_2_36_aarch64'),
                     lambda v: v['wheelCompatibility']['groups'][0].update(pythonTag='cp311\nanything'),
                     lambda v: v['python'].update(version='3.11\nanything'),
                     lambda v: v.update(hostWorkspace='/secret'),
                     lambda v: v['wheelCompatibility'].update(groups=[])]
        for mutate in mutations:
            value = copy.deepcopy(EVIDENCE)
            mutate(value)
            with self.assertRaises(ValueError):
                validate_evidence(json.dumps(value).encode())

    def test_dispatch_rejects_model_supplied_image_command_and_workspace(self):
        controller = Mock()
        request = {'schemaVersion': 1, 'action': 'capabilities', 'runtime': 'python'}
        for extra in ({'image': IMAGE}, {'project': 'project'}, {'command': 'uname'}, {'runtime': 'npm'}):
            with self.assertRaises(ValueError):
                dispatch_project(controller, {**request, **extra})
        controller.capabilities.assert_not_called()
        dispatch_project(controller, request)
        controller.capabilities.assert_called_once_with('python')

    def test_capability_read_does_not_grant_execution_after_revocation(self):
        with patch('project_authority.read_verified_access', return_value=False) as read:
            policy = ManagedFullAccessPolicy()
            self.assertTrue(policy(None, 'capabilities'))
            read.assert_not_called()
            self.assertFalse(policy('project', 'execute'))
            read.assert_called_once()


class CapabilityCacheTests(unittest.TestCase):
    def test_failed_cleanup_blocks_new_probe_until_same_resource_is_resolved(self):
        with tempfile.TemporaryDirectory() as root:
            controller = ProjectController(root, Path(root) / 'state', IMAGE,
                                           authorize=lambda *_: True, python_image=IMAGE)
            pending = ProbeCleanupPending(IMAGE, 'ods-project-capabilities-' + '1' * 24)
            try:
                with patch('project_controller.probe_python_runtime',
                           side_effect=[pending, copy.deepcopy(EVIDENCE)]) as probe, \
                        patch('project_controller.cleanup_pending_probe',
                              side_effect=[OSError('Docker offline'), OSError('still offline'), None]) as cleanup:
                    controller.initialize_capabilities()
                    for _ in range(2):
                        controller.initialize_capabilities()
                        self.assertEqual(probe.call_count, 1)
                    controller.initialize_capabilities()
                    self.assertEqual(probe.call_count, 2)
                    self.assertEqual(controller.capabilities('python')['status'], 'ready')
                    self.assertEqual(cleanup.call_count, 3)
                    self.assertTrue(all(call.args == (pending.image, pending.name) for call in cleanup.call_args_list))
            finally:
                controller.close()
    def test_background_failure_recovers_without_restart_or_tool_trigger(self):
        with tempfile.TemporaryDirectory() as root:
            controller = ProjectController(root, Path(root) / 'state', IMAGE,
                                           authorize=lambda *_: True, python_image=IMAGE)
            try:
                with patch('project_controller.probe_python_runtime',
                           side_effect=[OSError('Docker temporarily unavailable'), copy.deepcopy(EVIDENCE)]) as probe:
                    controller.start_capability_probe()
                    first_thread = controller._capability_thread
                    controller.start_capability_probe()
                    self.assertIs(controller._capability_thread, first_thread)
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline and controller.capabilities('python')['status'] != 'ready':
                        time.sleep(0.02)
                    self.assertEqual(controller.capabilities('python')['status'], 'ready')
                    self.assertEqual(probe.call_count, 2)
            finally:
                controller.close()
            self.assertFalse(controller._capability_thread.is_alive())

    def test_changed_image_discards_inflight_old_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            controller = ProjectController(root, Path(root) / 'state', IMAGE,
                                           authorize=lambda *_: True, python_image=IMAGE)
            replacement = 'sha256:' + 'b' * 64
            def changed(_image, **_kwargs):
                controller.python_image = replacement
                return copy.deepcopy(EVIDENCE)
            try:
                with patch('project_controller.probe_python_runtime', side_effect=changed):
                    controller.initialize_capabilities()
                self.assertEqual(controller.capabilities('python')['status'], 'unavailable')
                with patch('project_controller.probe_python_runtime', return_value=copy.deepcopy(EVIDENCE)) as probe:
                    controller.initialize_capabilities()
                    self.assertEqual(controller.capabilities('python')['image'], replacement)
                    probe.assert_called_once_with(replacement, cancel=None)
            finally:
                controller.close()
    def test_cached_identity_is_not_reprobed_or_reused_for_changed_image(self):
        with tempfile.TemporaryDirectory() as root:
            controller = ProjectController(root, Path(root) / 'state', IMAGE,
                                           authorize=lambda *_: True, python_image=IMAGE)
            try:
                self.assertEqual(controller.capabilities('python')['status'], 'unavailable')
                with patch('project_controller.probe_python_runtime', return_value=copy.deepcopy(EVIDENCE)) as probe:
                    controller.initialize_capabilities()
                    controller.initialize_capabilities()
                    first = controller.capabilities('python')
                    self.assertEqual(first['platform']['machine'], 'aarch64')
                    first['platform']['machine'] = 'mutated'
                    self.assertEqual(controller.capabilities('python')['platform']['machine'], 'aarch64')
                    probe.assert_called_once_with(IMAGE, cancel=None)
                    controller.python_image = 'sha256:' + 'b' * 64
                    self.assertEqual(controller.capabilities('python')['reason'], 'runtime-probe-unavailable')
                    probe.assert_called_once()
            finally:
                controller.close()

    def test_uninstalled_and_failed_probe_do_not_invent_identity_or_create_jobs(self):
        with tempfile.TemporaryDirectory() as root:
            controller = ProjectController(root, Path(root) / 'state', IMAGE, authorize=lambda *_: True)
            try:
                controller.initialize_capabilities()
                self.assertEqual(controller.capabilities('python')['reason'], 'runtime-not-installed')
                controller.python_image = IMAGE
                with patch('project_controller.probe_python_runtime', side_effect=OSError('Docker offline')) as probe:
                    controller.initialize_capabilities()
                    controller.initialize_capabilities()
                    result = controller.capabilities('python')
                    self.assertEqual(result['status'], 'unavailable')
                    self.assertNotIn('image', result)
                    self.assertNotIn('platform', result)
                    self.assertEqual(probe.call_count, 2)
                self.assertEqual(controller.futures, {})
            finally:
                controller.close()


@unittest.skipUnless(os.environ.get('ODS_TEST_PROJECT_PYTHON') == '1', 'real Docker opt-in')
class RealImageCapabilityTests(unittest.TestCase):
    def test_actual_image_reports_complete_python_and_wheel_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            iid = Path(directory) / 'image'
            subprocess.run(['docker', 'build', '--iidfile', str(iid), '-f',
                            str(HOST / 'Dockerfile.project-python'), str(HOST)],
                           check=True, capture_output=True, timeout=180)
            image = iid.read_text().strip()
            evidence = probe_python_runtime(image)
            # Exercise the present-resource cleanup path against real Docker,
            # too; a normal successful --rm probe usually takes absence path.
            name = 'ods-project-capabilities-' + uuid.uuid4().hex[:24]
            create = probe_arguments(image, name)
            create[1] = 'create'
            create.remove('--rm')
            subprocess.run(create, capture_output=True, check=True, timeout=15)
            try:
                cleanup_pending_probe(image, name)
                missing = subprocess.run(['docker', 'inspect', name], capture_output=True, timeout=5)
                self.assertNotEqual(missing.returncode, 0)
            finally:
                subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=5)
        self.assertEqual(evidence['platform']['os'], 'linux')
        self.assertTrue(evidence['python']['version'].startswith('3.11.'))
        self.assertEqual(evidence['python']['implementation'], 'cpython')
        self.assertEqual(evidence['python']['cacheTag'], 'cpython-311')
        groups = evidence['wheelCompatibility']['groups']
        self.assertTrue(any(group['pythonTag'] == 'py3' and group['abiTag'] == 'none'
                            and 'any' in group['platformTags'] for group in groups))
        self.assertEqual(evidence['wheelCompatibility']['tagCount'], sum(len(group['platformTags']) for group in groups))
        containers = subprocess.check_output(['docker', 'ps', '-a', '--filter', 'name=ods-project-capabilities-', '--format', '{{.Names}}'])
        self.assertEqual(containers.strip(), b'')


if __name__ == '__main__':
    unittest.main()
