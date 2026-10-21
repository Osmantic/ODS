"""Actual POSIX child/descriptor custody; synthetic CLI body, no Docker or model."""
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('native_lifecycle', ROOT / 'installers/macos/lib/pixel-native-lifecycle.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
pytestmark = pytest.mark.skipif(not hasattr(os, 'getuid') or os.getuid() == 0,
    reason='requires a real unprivileged POSIX descriptor owner')


def setup(tmp_path, *, held=True, body_fault=None, bash=None):
    install = tmp_path / 'ods'
    preparation = install / 'data/pixel-native/preparation'
    attempt = preparation / ('ingress-refresh-' + 'a' * 32)
    attempt.mkdir(mode=0o700, parents=True)
    journal = attempt / 'refresh.json'
    pointer = preparation / 'ingress-refresh-current.json'
    pointer.write_text(json.dumps({'directory': attempt.name}))
    pointer.chmod(0o600)
    descriptor = os.open(preparation / '.selection.lock', os.O_CREAT | os.O_RDWR, 0o600)
    if held: fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    cli = install / 'installers/macos/ods-macos.sh'
    cli.parent.mkdir(parents=True)
    command = '"$ODS_PYTHON_CMD" ' + shlex.quote(str(Path(module.__file__))) + ' --verify-child --install-dir ' + shlex.quote(str(install)) + ' --action restart --service ""'
    if body_fault == 'token': command = 'export ODS_NATIVE_LIFECYCLE_TOKEN=' + '0' * 64 + '\n' + command
    if body_fault == 'fd': command = 'export ODS_NATIVE_LIFECYCLE_FD=0\n' + command
    if body_fault == 'subshell': command = 'flags=$(' + command + ' --frozen-flags)\n[[ "$flags" == -f* ]]'
    if body_fault == 'action': command = command.replace('--action restart', '--action update')
    gate = ('while [[ ! -f ' + shlex.quote(str(install / 'release-child')) + ' ]]; do sleep 0.02; done\n'
        if body_fault == 'orphan' else 'sleep 0.1\n')
    cli.write_text('#!/bin/bash\nset -eu\n' + gate + command + '\nprintf "verified-body\\n"\n' + ('sleep 10\n' if body_fault == 'timeout' else ''))
    cli.chmod(0o700)
    (install / '.env').write_text('NATIVE=fixture\n')
    document = {'name': 'ods', 'services': {'pixel-native-ingress': {'image': 'sha256:' + 'b' * 64}}}
    def docker_run(*args, **kwargs):
        if args[:2] == ('image', 'inspect'):
            return SimpleNamespace(returncode=0, stdout='sha256:' + 'b' * 64)
        if args[0] == 'compose':
            return SimpleNamespace(returncode=0, stdout='pixel-native-ingress ' + 'c' * 64)
        if args[0] == 'ps':
            return SimpleNamespace(returncode=0, stdout='d' * 64)
        if args[0] == 'inspect':
            return SimpleNamespace(returncode=0, stdout=json.dumps([{'Id': 'd' * 64,
                'Image': 'sha256:' + 'b' * 64, 'Config': {'Labels': {
                    'com.docker.compose.project': 'ods', 'com.docker.compose.service': 'pixel-native-ingress',
                    'com.docker.compose.config-hash': 'c' * 64}}, 'State': {'Status': 'running'}}]))
        raise AssertionError(args)
    operation, kind, key = module.cli_operation(install_dir=install, action='restart', service='',
        bash=str(Path(bash or os.environ.get('ODS_LIFECYCLE_TEST_BASH', '/bin/bash')).resolve()),
        descriptor=descriptor, process_env=dict(os.environ), read_selection=lambda: ([], document),
        run=lambda *a, **k: SimpleNamespace(returncode=0, stdout=''), docker_run=docker_run)
    record = {'phase': 'operating', 'project': 'ods', 'operation': {'kind': kind, 'key': key}}
    def checkpoint(value):
        journal.write_text(json.dumps(value))
        journal.chmod(0o600)
    checkpoint(record)
    return install, descriptor, cli, record, checkpoint, operation


@pytest.mark.parametrize('fault', [None, 'subshell', 'token', 'fd', 'action', 'unheld', 'source'])
@pytest.mark.parametrize('bash', sorted({'/bin/bash', os.environ.get('ODS_LIFECYCLE_TEST_BASH', '/bin/bash')}))
def test_only_bound_parent_child_and_real_inherited_lease_can_enter_body(tmp_path, fault, bash):
    install, descriptor, cli, record, checkpoint, operation = setup(tmp_path,
        held=fault != 'unheld', body_fault=fault, bash=bash)
    try:
        if fault == 'source': cli.write_text(cli.read_text() + '# changed\n')
        if fault not in (None, 'subshell'):
            with pytest.raises(ValueError): operation({}, record, checkpoint)
        else:
            assert operation({}, record, checkpoint) == {'action': 'restart', 'service': '', 'exitCode': 0}
            assert 'verified-body' in Path(record['child']['log']).read_text()
        if fault not in (None, 'subshell') and 'child' in record:
            assert 'verified-body' not in Path(record['child']['log']).read_text()
    finally:
        os.close(descriptor)


def test_direct_internal_dispatch_has_no_ambient_bypass(tmp_path):
    install, descriptor, _, _, _, _ = setup(tmp_path)
    try:
        result = subprocess.run([sys.executable, str(Path(module.__file__)), '--verify-child',
            '--install-dir', str(install), '--action', 'restart'], capture_output=True,
            env={key: value for key, value in os.environ.items() if key not in module.ENV_KEYS}, timeout=5)
        assert result.returncode != 0 and 'no verified live transaction' in result.stderr.decode()
    finally:
        os.close(descriptor)


def test_observation_timeout_does_not_kill_child_or_release_its_inherited_lease(tmp_path, monkeypatch):
    install, descriptor, _, record, checkpoint, operation = setup(tmp_path, body_fault='timeout')
    original = module.subprocess.Popen
    children = []
    class ObservedTimeout:
        def __init__(self, *args, **kwargs):
            self.child = original(*args, **kwargs)
            self.pid = self.child.pid
            children.append(self.child)
        def wait(self, timeout):
            raise subprocess.TimeoutExpired('controlled observer', timeout)
    monkeypatch.setattr(module.subprocess, 'Popen', ObservedTimeout)
    # ps uses subprocess.run internally; retain that read-only path's real Popen.
    monkeypatch.setattr(module, 'process_identity', lambda pid: str(pid) + ' bounded-test-identity')
    monkeypatch.setattr(module, 'process_birth', lambda pid: str(pid) + ' bounded-test-birth')
    try:
        with pytest.raises(ValueError, match='child-unconfirmed'): operation({}, record, checkpoint)
        assert record['child']['outcome'] == 'unconfirmed'
        assert children[0].poll() is None
        os.close(descriptor)
        descriptor = None
        probe = os.open(install / 'data/pixel-native/preparation/.selection.lock', os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError): fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(probe)
    finally:
        for child in children:
            if child.poll() is None: child.terminate()
            child.wait(timeout=5)
        if descriptor is not None: os.close(descriptor)


def test_actual_parent_death_keeps_lease_until_orphan_refuses_body(tmp_path):
    driver = '''import importlib.util,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('fixture',sys.argv[1])
fixture=importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture)
install,descriptor,cli,record,checkpoint,operation=fixture.setup(Path(sys.argv[2]),body_fault='orphan')
operation({},record,checkpoint)
'''
    owner = subprocess.Popen([sys.executable, '-c', driver, str(Path(__file__)), str(tmp_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    install = tmp_path / 'ods'
    journal = install / 'data/pixel-native/preparation' / ('ingress-refresh-' + 'a' * 32) / 'refresh.json'
    selected = None
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                selected = json.loads(journal.read_text())
                if selected.get('child'): break
            except (OSError, ValueError): pass
            assert owner.poll() is None
            time.sleep(0.02)
        assert selected and selected.get('child'), 'owner never recorded its actual Bash child'
        owner.terminate()
        owner.wait(timeout=5)
        descriptor = os.open(install / 'data/pixel-native/preparation/.selection.lock', os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError): fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            (install / 'release-child').touch()
            deadline = time.monotonic() + 10
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    assert time.monotonic() < deadline, 'orphan did not release its inherited descriptor'
                    time.sleep(0.02)
            log = Path(selected['child']['log']).read_text()
            assert 'no verified live transaction' in log and 'verified-body' not in log
        finally: os.close(descriptor)
    finally:
        (install / 'release-child').touch()
        if owner.poll() is None: owner.terminate()
        owner.wait(timeout=5)


def test_unconfirmed_preparation_requires_actual_previous_child_exit(tmp_path):
    install, descriptor, _, record, checkpoint, _ = setup(tmp_path)
    directory = install / 'data/pixel-native/preparation' / ('ingress-refresh-' + 'a' * 32)
    child = None
    try:
        with pytest.raises(ValueError, match='child-unconfirmed'):
            module.execute([sys.executable, '-c', 'import time; time.sleep(10)'], env=dict(os.environ),
                descriptor=descriptor, record=record, field='preparationCommand', checkpoint=checkpoint,
                directory=directory, timeout=0.5)
        child = record['preparationCommand']['pid']
        with pytest.raises(ValueError, match='previous-child-still-live'): module.require_previous_gone(record)
        os.kill(child, signal.SIGTERM)
        os.waitpid(child, 0)
        child = None
        module.require_previous_gone(record)
    finally:
        if child is not None:
            os.kill(child, signal.SIGTERM)
            os.waitpid(child, 0)
        os.close(descriptor)
