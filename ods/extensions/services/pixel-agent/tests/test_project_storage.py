"""Temporary-only resource policy tests; Docker cases explicitly opt in."""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

HOST = Path(__file__).resolve().parents[1] / 'host'
sys.path.insert(0, str(HOST))
import project_storage as storage  # noqa: E402
from project_runtime import stage_arguments, deadline_command, start_keeper, recover_job  # noqa: E402
from project_controller import ProjectController  # noqa: E402


def job(number):
    return 'ods-project-' + format(number, '024x')


@pytest.fixture
def policy(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    monkeypatch.setattr(storage, 'engine_headroom', lambda _: (16 * 1024**3, 16 * 1024**3))
    monkeypatch.setattr(storage, 'engine_storage_jobs', set)
    return storage.ProjectStorage(tmp_path, job_bytes=32 * storage.MIB,
                                  total_bytes=64 * storage.MIB, max_jobs=2)


def test_reservations_are_durable_and_bound_bytes_and_jobs(policy):
    policy.reserve('image', job(1))
    restarted = storage.ProjectStorage(policy.state, job_bytes=32 * storage.MIB,
                                       total_bytes=64 * storage.MIB, max_jobs=2)
    restarted.reserve('image', job(2))
    with pytest.raises(ValueError, match='capacity reserved'):
        restarted.reserve('image', job(3))
    with pytest.raises(ValueError, match='already exists'):
        restarted.reserve('image', job(1))
    assert sum(restarted._read().values()) == 64 * storage.MIB


def test_byte_limit_is_independent_of_job_limit(policy):
    policy.total_bytes = 32 * storage.MIB
    policy.reserve('image', job(1))
    with pytest.raises(ValueError, match='capacity reserved'):
        policy.reserve('image', job(2))


def test_headroom_failure_never_creates_reservation(policy, monkeypatch):
    monkeypatch.setattr(storage, 'engine_headroom', lambda _: (16 * 1024**3, 100 * storage.MIB))
    with pytest.raises(ValueError, match='headroom'):
        policy.reserve('image', job(1))
    assert policy._read() == {}


def test_unknown_engine_volume_prevents_fresh_reservations(policy, monkeypatch):
    monkeypatch.setattr(storage, 'engine_storage_jobs', lambda: {job(9)})
    with pytest.raises(ValueError, match='unaccounted'):
        policy.reserve('image', job(1))
    assert policy._read() == {}


def test_remaining_or_unavailable_resources_keep_reservation(policy, monkeypatch):
    policy.reserve('image', job(1))
    monkeypatch.setattr(storage.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess([], 0, b'container\n'))
    with pytest.raises(ValueError, match='resources remain'):
        policy.release_removed(job(1))
    assert job(1) in policy._read()
    def unavailable(*args, **kwargs):
        raise subprocess.TimeoutExpired('docker', 5)
    monkeypatch.setattr(storage.subprocess, 'run', unavailable)
    with pytest.raises(subprocess.TimeoutExpired):
        policy.release_removed(job(1))
    assert job(1) in policy._read()
    monkeypatch.setattr(storage.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess([], 0, b''))
    policy.release_removed(job(1))
    assert policy._read() == {}


@pytest.mark.parametrize('change', [
    {'Driver': 'foreign'}, {'Options': {}}, {'Labels': {}}, {'Scope': 'global'},
    {'Name': job(2)}, {'Options': {'type': 'tmpfs', 'device': 'tmpfs', 'o': 'size=999999999999'}},
])
def test_volume_identity_requires_exact_bound_policy(change):
    value = {'Name': job(1), 'Driver': 'local', 'Scope': 'local',
             'Options': storage.volume_options(32 * storage.MIB),
             'Labels': {'org.osmantic.ods.project-job': job(1), 'org.osmantic.ods.project-storage': 'tmpfs-v1'}}
    assert storage.verify_volume(value, job(1), 32 * storage.MIB)
    assert not storage.verify_volume({**value, **change}, job(1))


def test_stage_deadline_and_swap_limit_are_controller_owned():
    image = 'sha256:' + 'a' * 64
    for stage in ('acquire', 'test', 'build'):
        args = stage_arguments(image, job(1), stage)
        assert args[args.index(image) + 1:][:4] == deadline_command([])
        assert args[args.index('--memory-swap') + 1] == '2g'
    assert stage_arguments(image, job(1), 'keeper')[-2:] == ['sleep', str(storage.KEEPER_SECONDS)]


@pytest.fixture
def real_image(tmp_path):
    if os.environ.get('ODS_TEST_PROJECT_NODE') != '1':
        pytest.skip('real Docker opt-in')
    iid = tmp_path / 'image'
    subprocess.run(['docker', 'build', '--iidfile', str(iid), '-f', str(HOST / 'Dockerfile.project-node'), str(HOST)],
                   capture_output=True, timeout=180, check=True)
    return iid.read_text().strip()


def test_real_tmpfs_survives_stages_hits_enospc_and_releases_resources(real_image, tmp_path):
    controller = ProjectController(tmp_path, tmp_path / 'state', real_image, authorize=lambda *_: True,
                                   storage_limits={'job_bytes': 32 * storage.MIB,
                                                   'total_bytes': 64 * storage.MIB, 'max_jobs': 2})
    identity = 'ods-project-' + uuid.uuid4().hex[:24]
    memory_during = None
    try:
        controller.storage.reserve(real_image, identity)
        controller.storage.create_volume(identity)
        start_keeper(real_image, identity)
        inode_limit = subprocess.check_output(['docker', 'exec', identity + '-keeper', 'node', '-e',
                                               "console.log(require('fs').statfsSync('/home/node').files)"], timeout=10)
        assert int(inode_limit) == storage.MAX_INODES
        for stage, code in [('test', "require('fs').writeFileSync('kept',Buffer.alloc(24*1024*1024,1))"),
                            ('build', "const f=require('fs');if(f.statSync('kept').size!==24*1024*1024)process.exit(2);try{f.writeFileSync('excess',Buffer.alloc(16*1024*1024));process.exit(3)}catch(e){if(e.code!=='ENOSPC')throw e;console.log(e.code)}")]:
            args = stage_arguments(real_image, identity, stage)
            result = subprocess.run([*args[:args.index(real_image) + 1], *deadline_command(['node', '-e', code])],
                                    capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, result.stderr
            if stage == 'build':
                assert 'ENOSPC' in result.stdout
        # Commands intentionally differ from controller-owned stages. Recovery
        # must refuse to stop/adopt them, even with matching image and labels.
        assert recover_job(real_image, identity, cancel=True)['evidence'] == 'identity-mismatch'
        raw = subprocess.check_output(['docker', 'exec', identity + '-keeper', 'cat', '/proc/meminfo'], timeout=10)
        memory_during = int(next(line.split()[1] for line in raw.splitlines() if line.startswith(b'Shmem:'))) * 1024
    finally:
        # Own test-created commands only; production cleanup correctly refuses them.
        for stage in ('test', 'build', 'keeper'):
            subprocess.run(['docker', 'rm', '-f', identity + '-' + stage], capture_output=True, timeout=15)
        subprocess.run(['docker', 'volume', 'rm', identity], capture_output=True, timeout=15, check=True)
        controller.storage.release_removed(identity)
        controller.close()
    assert controller.storage._read() == {}
    raw = subprocess.check_output(['docker', 'run', '--rm', '--network', 'none', '--read-only',
                                   '--user', '1000:1000', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                                   '--memory', '32m', '--memory-swap', '32m', '--pids-limit', '16',
                                   real_image, 'cat', '/proc/meminfo'], timeout=10)
    memory_after = int(next(line.split()[1] for line in raw.splitlines() if line.startswith(b'Shmem:'))) * 1024
    print(json.dumps({'quotaBytes': 32 * storage.MIB, 'engineShmemDuring': memory_during,
                      'engineShmemAfter': memory_after, 'observedShmemReleased': memory_during - memory_after,
                      'allOwnTmpfsReferencesRemoved': True}))


def test_real_deadline_kills_descendants_without_controller(real_image):
    name = 'ods-project-deadline-qa-' + uuid.uuid4().hex[:12]
    code = "require('child_process').spawn(process.execPath,['-e',\"process.on('SIGTERM',()=>{});setInterval(()=>{},100)\"],{detached:true});process.on('SIGTERM',()=>{});setInterval(()=>{},100)"
    try:
        result = subprocess.run(['docker', 'run', '--name', name, '--network', 'none', '--read-only',
                                 '--user', '1000:1000', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                                 '--memory', '64m', '--memory-swap', '64m', '--pids-limit', '32', real_image,
                                 *deadline_command(['node', '-e', code], 1)], capture_output=True, timeout=12)
        assert result.returncode in (124, 137)
        value = json.loads(subprocess.check_output(['docker', 'inspect', name]))[0]
        assert value['State']['Running'] is False
        assert subprocess.run(['docker', 'top', name], capture_output=True).returncode != 0
    finally:
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=15)


@pytest.mark.skipif(os.environ.get('ODS_TEST_PROJECT_NEXT') != '1', reason='real Next dependency acquisition opt-in')
def test_real_next_fits_configured_tmpfs_and_releases_its_reservation(real_image, tmp_path, monkeypatch):
    import project_controller
    package = {'private': True, 'scripts': {'test': 'node check.cjs', 'build': 'next build --webpack'},
               'dependencies': {'next': '16.3.6', 'react': '19.2.0', 'react-dom': '19.2.0'}}
    lock_name = 'ods-project-next-lock-qa-' + uuid.uuid4().hex[:12]
    lock_program = "const f=require('fs');f.writeFileSync('package.json',f.readFileSync(0));require('child_process').execFileSync('npm',['install','--package-lock-only','--ignore-scripts','--no-audit','--no-fund','--registry=https://registry.npmjs.org'],{stdio:['ignore',2,2]});process.stdout.write(f.readFileSync('package-lock.json'))"
    try:
        lock = subprocess.run(['docker', 'run', '-i', '--name', lock_name, '--network', 'bridge',
                               '--read-only', '--user', '1000:1000', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                               '--memory', '512m', '--memory-swap', '512m', '--pids-limit', '64', '--cpus', '1',
                               '--tmpfs', '/workspace:rw,nosuid,nodev,size=32m,uid=1000,gid=1000',
                               '--tmpfs', '/tmp:rw,nosuid,nodev,size=256m', '--workdir', '/workspace',
                               real_image, *deadline_command(['node', '-e', lock_program], 180)],
                              input=json.dumps(package), capture_output=True, text=True, timeout=190, check=True)
    finally:
        subprocess.run(['docker', 'rm', '-f', lock_name], capture_output=True, timeout=15)
    assert len(lock.stdout) < 4 * storage.MIB
    project = tmp_path / 'next-project'
    project.mkdir()
    (project / 'pages').mkdir()
    (project / 'package.json').write_text(json.dumps(package))
    (project / 'package-lock.json').write_text(lock.stdout)
    (project / 'check.cjs').write_text("require('assert').equal(require('react').createElement('h1',null,'bounded').type,'h1')")
    (project / 'next.config.mjs').write_text('export default {output:"export",assetPrefix:".",images:{unoptimized:true}};')
    (project / 'pages/index.jsx').write_text('export default function Page(){return <h1>Bounded Portal build</h1>}')
    controller = ProjectController(tmp_path, tmp_path / 'state', real_image, authorize=lambda *_: True)
    samples = []
    original_stage = project_controller.run_stage
    def measured_stage(image, identity, stage, **kwargs):
        value = original_stage(image, identity, stage, **kwargs)
        measured = subprocess.run(['docker', 'exec', identity + '-keeper', 'du', '-sb', '/home/node'],
                                  capture_output=True, text=True, timeout=10, check=True)
        samples.append({'stage': stage, 'status': value['status'], 'storedBytes': int(measured.stdout.split()[0])})
        return value
    monkeypatch.setattr(project_controller, 'run_stage', measured_stage)
    try:
        receipt = controller.submit('f' * 64, 'next-project')
        controller.futures[receipt['id']].result(timeout=750)
        receipt = controller.observe(receipt['id'])
        assert receipt['state'] == 'succeeded', receipt
        index = tmp_path / receipt['output']['relativeDirectory'] / 'index.html'
        assert 'Bounded Portal build' in index.read_text()
        assert controller.storage._read() == {}
        assert not subprocess.check_output(['docker', 'ps', '-aq', '--filter', 'label=org.osmantic.ods.project-job=' + receipt['id']]).strip()
        assert subprocess.run(['docker', 'volume', 'inspect', receipt['id']], capture_output=True).returncode != 0
        print(json.dumps({'nextStageSamples': samples, 'quotaBytes': controller.storage.job_bytes,
                          'reservationReleased': True, 'tmpfsVolumeAndMountingContainersRemoved': True}))
    finally:
        controller.close()
