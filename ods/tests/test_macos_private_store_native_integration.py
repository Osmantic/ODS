"""Real native child/lease and store dispatcher; no Docker daemon or model."""
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
from types import SimpleNamespace

import pytest

import test_macos_dashboard_private_state as storage_tests

ODS, W = storage_tests.ODS, storage_tests.W


@pytest.fixture
def installed():
    fixture = storage_tests.PublicCommands()
    fixture.setUp()
    try:
        root = fixture.root
        (root / ".env").write_text("NATIVE=fixture\n")
        library = root / 'installers/macos/lib'
        for name in ('pixel-native-lifecycle', 'pixel-native-lifecycle-stack',
                     'pixel-native-lifecycle-inputs', 'pixel-native-env'):
            shutil.copy2(ODS / 'installers/macos/lib' / (name + '.py'), library)
        values = root / 'extensions/services/dashboard-api/env_values.py'
        values.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ODS / 'extensions/services/dashboard-api/env_values.py', values)
        spec = importlib.util.spec_from_file_location('installed_lifecycle', library / 'pixel-native-lifecycle.py')
        lifecycle = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lifecycle)
        yield fixture, lifecycle
    finally:
        fixture.doCleanups()


@pytest.mark.parametrize('action', ['start', 'restart', 'update'])
@pytest.mark.parametrize('extra_recipe', [False, True])
def test_frozen_native_child_holds_storage_lock_and_replays_only_exact_recipe(installed, action, extra_recipe):
    fixture, lifecycle = installed
    root = fixture.root
    state = root / 'docker-fixture.json'
    data = json.loads(state.read_text())
    image = 'sha256:' + 'a' * 64
    data['config']['services']['dashboard-api']['image'] = image
    data['config']['services']['pixel-native-ingress'] = {'image': image, 'pull_policy': 'never'}
    data['config']['services']['dashboard-api']['pull_policy'] = 'never'
    data['require_native'] = True
    state.write_text(json.dumps(data))
    docker = fixture.fixture.bin / 'pinned-docker'
    shutil.copy2(fixture.fixture.bin / 'docker', docker)
    cli = root / 'installers/macos/ods-macos.sh'
    helper = root / 'installers/macos/lib/dashboard-private-state.py'
    flags_command = (f'"$ODS_PYTHON_CMD" {shlex.quote(str(Path(lifecycle.__file__)))} '
                     f'--install-dir {shlex.quote(str(root))} --verify-child --action {action} --frozen-flags')
    cli.write_text('#!/bin/bash\nset -eu\nflags=$(' + flags_command + ')\n' +
                   ('flags="$flags -f installers/macos/dashboard-private-state.compose.yaml"\n' if extra_recipe else '') +
                   f'"$ODS_PYTHON_CMD" {shlex.quote(str(helper))} launch --native-lifecycle '
                   f'--install-dir {shlex.quote(str(root))} --operation {action} --flags="$flags"\n')
    cli.chmod(0o700)
    preparation = root / 'data/pixel-native/preparation'
    attempt = preparation / ('ingress-refresh-' + 'b' * 32)
    attempt.mkdir(parents=True, mode=0o700)
    pointer = preparation / 'ingress-refresh-current.json'
    pointer.write_text(json.dumps({'directory': attempt.name}))
    pointer.chmod(0o600)
    descriptor = os.open(preparation / '.selection.lock', os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    document = data['config']
    names = ['dashboard-api', 'pixel-native-ingress']
    def docker_run(*args, **kwargs):
        if args[:2] == ('image', 'inspect'):
            output = image
        elif args[0] == 'compose':
            output = '\n'.join(name + ' ' + 'c' * 64 for name in names)
        elif args[0] == 'ps':
            output = ('d' if 'service=dashboard-api' in ' '.join(args) else 'e') * 64
        elif args[0] == 'inspect':
            name = names[0 if args[1] == 'd' * 64 else 1]
            output = json.dumps([{'Id': args[1], 'Image': image, 'State': {'Status': 'running'},
                'Config': {'Labels': {'com.docker.compose.project': 'ods',
                    'com.docker.compose.service': name, 'com.docker.compose.config-hash': 'c' * 64}}}])
        else:
            raise AssertionError(args)
        return SimpleNamespace(returncode=0, stdout=output)
    operation, kind, key = lifecycle.cli_operation(install_dir=root, action=action, service='',
        bash=str(Path(shutil.which('bash')).resolve()), descriptor=descriptor,
        process_env={**fixture.fixture.env, 'DOCKER_HOST': 'unix:///fixture.sock'},
        read_selection=lambda: ([root / 'docker-compose.base.yml'], document),
        run=lambda *a, **k: SimpleNamespace(returncode=0, stdout=''), docker_run=docker_run,
        docker_binary=str(docker))
    record = {'phase': 'operating', 'project': 'ods', 'operation': {'kind': kind, 'key': key}}
    journal = attempt / 'refresh.json'
    def checkpoint(value):
        journal.write_text(json.dumps(value))
        journal.chmod(0o600)
    checkpoint(record)
    try:
        if extra_recipe:
            with pytest.raises(ValueError, match='child-failed'):
                operation({}, record, checkpoint)
            assert 'private-store-native-frozen-flags' in Path(record['child']['log']).read_text()
            assert not json.loads(state.read_text())['launches']
        else:
            assert operation({}, record, checkpoint)['exitCode'] == 0, Path(record['child']['log']).read_text()
            after = json.loads(state.read_text())
            assert after['builds'] == 0
            assert after['launches'] == [['up', '-d', '--wait', '--wait-timeout', '120', '--no-build',
                '--pull', 'never', *(['--force-recreate'] if action != 'start' else [])]]
            assert (fixture.image_path() / W.MARKER).is_file()
            assert after['native_prefix_verified'] and after['storage_lock_verified']
    finally:
        os.close(descriptor)


def test_native_freeze_includes_private_recipe_from_legacy_cache(installed):
    fixture, _ = installed
    root = fixture.root
    (root / '.compose-flags').write_text('-f docker-compose.base.yml')
    spec = importlib.util.spec_from_file_location('finalize_with_stores',
        ODS / 'installers/macos/lib/pixel-native-finalize.py')
    finalize = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(finalize)
    assert finalize.compose_flags(root, {}) == ['-f', 'docker-compose.base.yml',
        '-f', 'installers/macos/dashboard-private-state.compose.yaml']
    assert (root / '.compose-flags').read_text() == '-f docker-compose.base.yml'
