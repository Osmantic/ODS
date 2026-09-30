"""Exact preflight boundary, durable retry fence, and real local transport."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from project_controller import ProjectController
from project_jobs import ProjectJobs, ProjectRecoveryRequired
from project_diagnostics import diagnostic_digest
from project_storage import ProjectStorage
import test_project_transport as transport_tests


@pytest.fixture
def controller(tmp_path):
    value = ProjectController(tmp_path, tmp_path / 'state', 'sha256:' + 'a' * 64,
                              authorize=lambda *_: True)
    yield value
    value.close()


def request(controller, project='demo'):
    return {'project': project, 'sourceSha256': 'b' * 64,
            'image': controller.image, 'outputDirectory': 'out'}


def docker_info_failure(argv, **kwargs):
    if argv[1:3] == ['volume', 'ls']:
        return subprocess.CompletedProcess(argv, 0, b'')
    if argv[1] == 'run' and argv[-2:] == ['cat', '/proc/meminfo']:
        return subprocess.CompletedProcess(argv, 0, b'MemTotal: 16777216 kB\nMemAvailable: 16777216 kB\n')
    if argv == ['docker', 'info', '--format', '{{.MemTotal}}']:
        raise subprocess.CalledProcessError(2, argv, stderr=b'private daemon detail')
    pytest.fail(f'unexpected side effect: {argv}')


def test_real_reservation_query_failure_is_failed_without_resource_intent(controller):
    req = request(controller)
    job, _ = controller.jobs.create('a' * 64, req)
    with patch('project_storage.subprocess.run', side_effect=docker_info_failure), \
            patch.object(controller.storage, 'create_volume') as create, \
            patch.object(controller, '_cleanup') as cleanup:
        controller._work(job, req, {}, threading.Event())
    row = controller.jobs.observe(job)
    assert row['state'] == 'failed'
    assert row['steps'] == []
    assert row['output']['executionStarted'] is False
    assert row['output']['retryEligible'] is True
    assert row['output']['automaticRetry'] is False
    assert row['output']['code'] == 'engine-info-unavailable'
    assert 'private' not in json.dumps(row['output'])
    assert not (controller.storage.state / 'storage.json').exists()
    create.assert_not_called()
    cleanup.assert_not_called()
    # No automatic retry occurred. A new explicit authorized submission can run.
    retry, created = controller.jobs.create('c' * 64, req)
    assert created
    artifacts = {'sha256': 'c' * 64, 'files': {'index.html': b'ok'}, 'bytes': 2}
    relative = 'demo/ods-builds/' + retry.removeprefix('ods-project-') + '/site'
    with patch.object(controller.storage, 'reserve'), patch.object(controller.storage, 'create_volume'), \
            patch('project_controller.start_keeper'), patch('project_controller.seed_project'), \
            patch('project_controller.run_stage', return_value={'status': 'succeeded', 'exitCode': 0}), \
            patch('project_controller.collect_artifacts', return_value=artifacts), \
            patch('project_controller.import_artifacts', return_value=relative), \
            patch.object(controller, '_cleanup', return_value=[]):
        controller._work(retry, req, {}, threading.Event())
    assert controller.jobs.observe(retry)['state'] == 'succeeded'


def test_reservation_write_failure_stays_uncertain_across_restart(controller):
    req = request(controller)
    job, _ = controller.jobs.create('a' * 64, req)
    original = controller.storage._write
    def interrupted_write(value):
        original(value)
        raise OSError('crash after atomic reservation write')
    with patch('project_storage.engine_storage_jobs', return_value=set()), \
            patch('project_storage.engine_headroom', return_value=(16 * 1024**3, 16 * 1024**3)), \
            patch.object(controller.storage, '_write', side_effect=interrupted_write), \
            patch.object(controller.storage, 'create_volume') as create:
        controller._work(job, req, {}, threading.Event())
    create.assert_not_called()
    assert controller.jobs.observe(job)['state'] == 'unconfirmed'
    assert job in controller.storage._read()
    restarted = ProjectJobs(controller.storage.state)
    with pytest.raises(ProjectRecoveryRequired) as blocked:
        restarted.create('c' * 64, {**req, 'sourceSha256': 'd' * 64, 'outputDirectory': 'dist'})
    assert blocked.value.job == job
    with pytest.raises(ProjectRecoveryRequired):
        restarted.create('f' * 64, {**req, 'runtime': 'python'})
    assert restarted.create('a' * 64, req) == (job, False)
    other, _ = restarted.create('e' * 64, request(controller, 'independent'))
    assert restarted.claim(other)


def test_preexisting_queue_cannot_start_after_prior_job_becomes_uncertain(controller):
    req = request(controller)
    first, _ = controller.jobs.create('a' * 64, req)
    queued, _ = controller.jobs.create('b' * 64, req)
    controller.jobs.claim(first)
    controller.jobs.controller_failure(first, 'uncertain')
    assert not controller.jobs.claim(queued)
    row = controller.jobs.observe(queued)
    assert row['state'] == 'failed'
    assert row['output'] == {'code': 'recovery-required', 'recoveryJobId': first,
                             'executionStarted': False, 'retryEligible': False}
    assert controller.jobs.observe(first)['state'] == 'unconfirmed'


def test_probe_run_timeout_never_claims_safe_retry(controller):
    req = request(controller)
    job, _ = controller.jobs.create('a' * 64, req)
    with patch('project_storage.engine_storage_jobs', return_value=set()), \
            patch('project_storage.subprocess.run', side_effect=subprocess.TimeoutExpired('docker run', 10)):
        controller._work(job, req, {}, threading.Event())
    assert controller.jobs.observe(job)['state'] == 'unconfirmed'


@pytest.mark.parametrize('failure', [OSError, PermissionError])
def test_diagnostic_reservation_write_uncertainty_cannot_retry_same_runtime(controller, failure):
    req = {'project': 'ods-diagnostic', 'kind': 'diagnostic', 'runtime': 'npm',
           'sourceSha256': diagnostic_digest('npm'), 'image': controller.image, 'outputDirectory': 'diagnostic'}
    job, _ = controller.jobs.create('a' * 64, req)
    original = ProjectStorage._write
    def interrupted_write(storage, value):
        original(storage, value)
        raise failure('after reservation rename')
    with patch('project_storage.engine_storage_jobs', return_value=set()), \
            patch('project_storage.engine_headroom', return_value=(16 * 1024**3, 16 * 1024**3)), \
            patch.object(ProjectStorage, '_write', interrupted_write):
        controller._diagnose(job, req, threading.Event())
    assert controller.jobs.observe(job)['state'] == 'unconfirmed'
    assert controller.jobs.observe(job)['output']['cleanup'] == 'unconfirmed'
    assert job in controller.storage._read()
    with pytest.raises(ProjectRecoveryRequired):
        controller.jobs.create('b' * 64, req)
    other, _ = controller.jobs.create('c' * 64, {**req, 'runtime': 'python',
        'sourceSha256': diagnostic_digest('python')})
    assert controller.jobs.claim(other)


def test_restart_marks_running_uncertain_before_accepting_other_turn(controller):
    req = request(controller)
    job, _ = controller.jobs.create('a' * 64, req)
    controller.jobs.claim(job)
    restarted = ProjectJobs(controller.storage.state)
    restarted.recover_interrupted()  # Actual service startup lifecycle.
    with pytest.raises(ProjectRecoveryRequired):
        restarted.create('b' * 64, req)
    assert restarted.observe(job)['state'] == 'unconfirmed'


def test_real_socket_fence_rechecks_owner_and_policy_before_disclosing_job(controller):
    req = request(controller)
    job, _ = controller.jobs.create('a' * 64, req)
    controller.jobs.claim(job)
    controller.jobs.controller_failure(job, 'uncertain')
    package = {'private': True}
    source = {'sha256': 'b' * 64, 'files': {
        'package.json': json.dumps(package).encode(),
        'package-lock.json': json.dumps({'lockfileVersion': 3, 'packages': {'': package}}).encode()}}
    exchange = transport_tests.ProjectTransportTests().exchange
    envelope = {'context': {'sessionId': 'new-session', 'toolCallId': 'new-call'},
                'request': {'schemaVersion': 1, 'action': 'submit', 'project': 'demo', 'outputDirectory': 'out'}}
    with patch('project_controller.snapshot_project', return_value=source), \
            patch.object(controller.pool, 'submit', return_value=Mock()) as work:
        blocked = exchange(controller, envelope)
        assert blocked['status'] == 'recovery-required' and blocked['jobId'] == job
        work.assert_not_called()
        denied = exchange(controller, envelope, owner=os.getuid() + 1)
        assert denied['status'] == 'denied' and job not in json.dumps(denied)
        controller.authorize = lambda *_: False
        denied = exchange(controller, envelope)
        assert denied['status'] == 'denied' and job not in json.dumps(denied)
        controller.authorize = lambda *_: True
        envelope['request']['project'] = 'independent'
        accepted = exchange(controller, envelope)
        assert accepted['status'] == 'queued'
        work.assert_called_once()
