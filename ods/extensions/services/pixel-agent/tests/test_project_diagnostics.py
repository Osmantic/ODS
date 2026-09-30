"""Typed, offline diagnostics use the existing owner/job/resource boundary."""
from pathlib import Path
from types import SimpleNamespace
import sys
import json
import threading
import os
import subprocess
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from project_dispatch import dispatch_project  # noqa: E402
from project_controller import ProjectController
from project_diagnostics import CHECKS, diagnostic_digest, validate_diagnostic
from project_runtime import stage_arguments
from project_jobs import ProjectJobs


def test_diagnostic_dispatch_is_typed_and_returns_a_separate_job_purpose():
    observed = []
    def diagnose(key, runtime):
        observed.append((key, runtime))
        return {'id': 'ods-project-' + 'a' * 24, 'state': 'queued', 'cancel_requested': False,
                'request': {'project': 'ods-diagnostic', 'kind': 'diagnostic', 'runtime': runtime},
                'steps': [], 'output': None}
    result = dispatch_project(SimpleNamespace(diagnose=diagnose),
                              {'schemaVersion': 1, 'action': 'diagnose', 'runtime': 'python'}, request_key='b' * 64)
    assert observed == [('b' * 64, 'python')]
    assert result['purpose'] == 'diagnostic'
    assert result['scope'] == 'managed-executor'
    assert result['project'] is None


@pytest.mark.parametrize('extra', [{'command': 'rm -rf /'}, {'path': '/tmp'}, {'package': 'arbitrary'}])
def test_diagnostic_rejects_commands_paths_and_packages(extra):
    with pytest.raises(ValueError):
        dispatch_project(SimpleNamespace(), {'schemaVersion': 1, 'action': 'diagnose', 'runtime': 'python', **extra},
                         request_key='a' * 64)


def test_other_runtime_is_explicitly_unsupported_without_execution():
    value = dispatch_project(SimpleNamespace(), {'schemaVersion': 1, 'action': 'diagnose', 'runtime': 'ruby'},
                             request_key='a' * 64)
    assert value['code'] == 'unsupported'
    assert value['supportedRuntimes'] == ['npm', 'python']


def report(runtime='python', **codes):
    return {'schemaVersion': 1, 'scope': 'managed-executor', 'runtime': runtime,
            'checks': {name: {'code': codes.get(name, 'ready')} for name in CHECKS}}


@pytest.mark.parametrize('runtime', ['npm', 'python'])
def test_fixed_stage_has_no_network_or_caller_code_and_native_deadline(runtime):
    args = stage_arguments('sha256:' + 'a' * 64, 'ods-project-' + 'b' * 24, 'diagnose', runtime=runtime)
    assert args[args.index('--network') + 1] == 'none'
    assert args[args.index('--user') + 1] == '1000:1000'
    assert '--read-only' in args and 'no-new-privileges' in args
    assert '30s' in args
    assert not any('docker.sock' in arg or '/workspace' in arg for arg in args)


@pytest.mark.parametrize('malformed', [[], {'code': []}, {'code': 'ready', 'version': '\nforged'}, {'code': 'missing', 'version': 'fake'}])
def test_invalid_probe_output_is_not_a_capability(malformed):
    value = report()
    value['checks']['python'] = malformed
    with pytest.raises(ValueError):
        validate_diagnostic(json.dumps(value), 'python')


@pytest.mark.parametrize('version', [True, False, 1.0, '1'])
def test_diagnostic_schema_requires_an_exact_integer(version):
    value = report()
    value['schemaVersion'] = version
    with pytest.raises(ValueError):
        validate_diagnostic(json.dumps(value), 'python')


def test_crash_after_diagnostic_stage_never_awaits_artifact_import(tmp_path):
    # Stop a real bookkeeping process after the durable stage write, before
    # the final diagnostic receipt. No container or artifact is invented.
    program = '''import os, pathlib, sys
sys.path.insert(0, sys.argv[1])
from project_jobs import ProjectJobs
from project_diagnostics import diagnostic_digest
root=pathlib.Path(sys.argv[2]); jobs=ProjectJobs(root/'state')
request={'project':'ods-diagnostic','kind':'diagnostic','runtime':'npm','sourceSha256':diagnostic_digest('npm'),
         'image':'sha256:'+'a'*64,'outputDirectory':'diagnostic'}
job,_=jobs.create('a'*64,request); jobs.claim(job)
jobs.record_stage(job,'diagnose',{'status':'succeeded','exitCode':0,'stdout':'fixture stage receipt'})
(root/'job-id').write_text(job)
os._exit(91)
'''
    result = subprocess.run([sys.executable, '-c', program,
                             str(Path(__file__).resolve().parents[1] / 'host'), str(tmp_path)], timeout=10)
    assert result.returncode == 91
    jobs = ProjectJobs(tmp_path / 'state')
    job = (tmp_path / 'job-id').read_text()
    assert jobs.reconcile(job)['runtime']['status'] == 'awaiting-diagnostic-receipt'
    assert jobs.observe(job)['state'] == 'running'
    assert jobs.observe(job)['output'] is None
    jobs.recover_interrupted()
    with patch('project_runtime.recover_job', return_value={'status': 'unconfirmed', 'evidence': 'unavailable'}) as recover:
        inspected = jobs.reconcile(job)
    assert inspected['job']['state'] == 'unconfirmed'
    assert inspected['runtime']['status'] == 'unconfirmed'
    assert inspected['job']['steps'][0]['stdout'] == 'fixture stage receipt'
    assert 'artifactImportUnconfirmed' not in inspected['job']['output']
    assert recover.call_args.kwargs['runtime'] == 'npm'


@pytest.mark.parametrize('missing', [None, 'venv', 'pip'])
@pytest.mark.parametrize('cleanup_failed', [False, True])
def test_owned_job_keeps_partial_checks_and_cleanup_failure(tmp_path, missing, cleanup_failed):
    controller = ProjectController(tmp_path, tmp_path / 'state', 'sha256:' + 'a' * 64,
                                   python_image='sha256:' + 'b' * 64, authorize=lambda *args: True)
    request = {'project': 'ods-diagnostic', 'kind': 'diagnostic', 'runtime': 'python',
               'sourceSha256': diagnostic_digest('python'), 'image': controller.python_image, 'outputDirectory': 'diagnostic'}
    job, _ = controller.jobs.create('a' * 64, request)
    value = report(**({missing: 'missing'} if missing else {}))
    try:
        with patch('project_controller.ProjectStorage.reserve'), patch('project_controller.ProjectStorage.create_volume'), \
                patch('project_controller.run_stage', return_value={'status': 'succeeded', 'exitCode': 0,
                      'stdout': json.dumps(value), 'truncated': {'stdout': False}}), \
                patch.object(controller, '_cleanup', return_value=['cleanup unconfirmed'] if cleanup_failed else []), \
                patch('project_controller.snapshot_project') as snapshot, patch('project_controller.import_artifacts') as imported:
            controller._diagnose(job, request, threading.Event())
        result = controller.observe(job)
        assert result['state'] == 'succeeded'  # Probe completed; individual checks carry their outcomes.
        assert result['output']['checks'] == value['checks']
        assert result['output']['code'] == ('missing' if missing else 'ready')
        assert result['output']['chatSandboxVerified'] is False
        assert result['output']['cleanup'] == ('unconfirmed' if cleanup_failed else 'confirmed')
        assert result['output']['scratchLimitBytes'] == 64 * 1024 * 1024
        if cleanup_failed:
            assert result['output']['nextAction']['action'] == 'cancel'
        snapshot.assert_not_called()
        imported.assert_not_called()
    finally:
        controller.close()


def test_diagnostic_denial_never_queues_or_touches_workspace(tmp_path):
    controller = ProjectController(tmp_path, tmp_path / 'state', 'sha256:' + 'a' * 64, authorize=lambda *args: False)
    try:
        with patch.object(controller.pool, 'submit') as queued, patch('project_controller.snapshot_project') as snapshot:
            with pytest.raises(PermissionError):
                controller.diagnose('a' * 64, 'npm')
        queued.assert_not_called()
        snapshot.assert_not_called()
    finally:
        controller.close()


def test_unconfirmed_diagnostic_retains_storage_and_recovery_names_exact_stage(tmp_path):
    controller = ProjectController(tmp_path, tmp_path / 'state', 'sha256:' + 'a' * 64, authorize=lambda *args: True)
    request = {'project': 'ods-diagnostic', 'kind': 'diagnostic', 'runtime': 'npm',
               'sourceSha256': diagnostic_digest('npm'), 'image': controller.image, 'outputDirectory': 'diagnostic'}
    job, _ = controller.jobs.create('a' * 64, request)
    try:
        with patch('project_controller.ProjectStorage.reserve'), patch('project_controller.ProjectStorage.create_volume'), \
                patch('project_controller.run_stage', return_value={'status': 'unconfirmed', 'exitCode': None}), \
                patch.object(controller, '_cleanup') as cleanup:
            controller._diagnose(job, request, threading.Event())
        cleanup.assert_not_called()
        assert controller.observe(job)['state'] == 'unconfirmed'
        assert controller.observe(job)['output']['cleanup'] == 'unconfirmed'
        controller.jobs.request_cancel(job)
        with patch('project_controller.recover_job', return_value={'status': 'unconfirmed', 'evidence': 'identity-mismatch'}) as recover, \
                patch.object(controller, '_cleanup') as cleanup:
            controller._recover_cancel(job, request)
        assert recover.call_args.kwargs['required_stage'] == 'diagnose'
        assert recover.call_args.args[0] == request['image']
        assert controller.observe(job)['state'] == 'unconfirmed'
        cleanup.assert_not_called()
        with patch('project_controller.recover_job', return_value={'status': 'cancelled', 'evidence': 'docker-state'}), \
                patch.object(controller, '_cleanup', return_value=[]):
            controller._recover_cancel(job, request)
        recovered = controller.observe(job)
        assert recovered['state'] == 'cancelled'
        assert recovered['output']['cleanup'] == 'confirmed'
        assert recovered['output']['runtimeRecovery']['evidence'] == 'docker-state'
        assert 'artifactImportUnconfirmed' not in recovered['output']
    finally:
        controller.close()


def test_terminal_diagnostic_cleanup_retry_updates_only_verified_cleanup(tmp_path):
    controller = ProjectController(tmp_path, tmp_path / 'state', 'sha256:' + 'a' * 64, authorize=lambda *args: True)
    request = {'project': 'ods-diagnostic', 'kind': 'diagnostic', 'runtime': 'npm',
               'sourceSha256': diagnostic_digest('npm'), 'image': controller.image, 'outputDirectory': 'diagnostic'}
    job, _ = controller.jobs.create('a' * 64, request)
    try:
        with patch('project_controller.ProjectStorage.reserve'), patch('project_controller.ProjectStorage.create_volume'), \
                patch('project_controller.run_stage', return_value={'status': 'succeeded', 'exitCode': 0,
                                                                  'stdout': json.dumps(report('npm'))}), \
                patch.object(controller, '_cleanup', return_value=['cleanup unconfirmed']):
            controller._diagnose(job, request, threading.Event())
        before = controller.observe(job)
        assert before['output']['cleanup'] == 'unconfirmed'
        with patch.object(controller, '_cleanup', return_value=[]) as cleanup:
            controller._retry_cleanup(job, request)
        after = controller.observe(job)
        assert after['state'] == before['state'] == 'succeeded'
        assert after['output']['checks'] == before['output']['checks']
        assert after['output']['cleanup'] == 'confirmed'
        assert 'cleanupWarnings' not in after['output']
        cleanup.assert_called_once_with(job, image=request['image'], runtime='npm')
    finally:
        controller.close()


@pytest.mark.skipif(os.environ.get('ODS_TEST_PROJECT_DIAGNOSTIC') != '1', reason='real disposable Docker opt-in')
@pytest.mark.parametrize('runtime', ['npm', 'python'])
def test_real_diagnostic_job_is_offline_and_leaves_no_owned_resources(tmp_path, runtime):
    host = Path(__file__).resolve().parents[1] / 'host'
    iid = tmp_path / 'image-id'
    dockerfile = host / ('Dockerfile.project-python' if runtime == 'python' else 'Dockerfile.project-node')
    subprocess.run(['docker', 'build', '--iidfile', str(iid), '-f', str(dockerfile), str(host)],
                   capture_output=True, check=True, timeout=180)
    image = iid.read_text().strip()
    controller = ProjectController(tmp_path, tmp_path / 'state', image,
                                   python_image=image if runtime == 'python' else None, authorize=lambda *args: True)
    job = None
    try:
        row = controller.diagnose('a' * 64, runtime)
        job = row['id']
        controller.futures[job].result(timeout=90)
        row = controller.observe(job)
        assert row['state'] == 'succeeded', row
        output = row['output']
        assert output['code'] == 'ready', output
        assert output['cleanup'] == 'confirmed', output
        assert output['chatSandboxVerified'] is False
        assert output['checks']['scratch']['code'] == 'ready'
        if runtime == 'python':
            assert output['checks']['venv']['code'] == 'ready'
            assert output['checks']['pip']['code'] == 'ready'
        assert controller.storage._read() == {}
        assert subprocess.run(['docker', 'volume', 'inspect', job], capture_output=True).returncode != 0
        assert subprocess.run(['docker', 'inspect', job + '-diagnose'], capture_output=True).returncode != 0
    finally:
        controller.close()
        if job is not None:
            # Controller cleanup authenticates exact image/argv/isolation before removal.
            controller._cleanup(job, image=image, runtime=runtime)
