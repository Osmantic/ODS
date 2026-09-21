import importlib.util
import json
import os
from pathlib import Path
import plistlib
import socket
import tempfile

import pytest


SPEC = importlib.util.spec_from_file_location('native_layout',
    Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-native-layout.py')
layout = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(layout)


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(layout.sys, 'platform', 'darwin')
    monkeypatch.setattr(layout.os, 'geteuid', lambda: 501)
    root = tmp_path.resolve()
    home = root / 'New Owner Home'
    candidate = root / 'candidate'
    candidate.mkdir(mode=0o700)
    document = {'agents': {'list': [{'id': 'pixel', 'workspace': str(home / '.openclaw/workspace-pixel')}],
        'defaults': {'sandbox': {'docker': {'binds': [str(home / '.openclaw/.ods-exec-control') + ':/run/pixel-ods-control:ro']}}}},
        'gateway': {'port': 18789}}
    for name, value in [('openclaw.json', document), ('candidate.json', {'status': 'staged',
        'pixelSourceRef': 'a' * 40, 'requiresServiceQualification': True})]:
        (candidate / name).write_text(json.dumps(value))
        (candidate / name).chmod(0o600)
    (candidate / 'workspace').mkdir()
    (candidate / 'workspace/AGENTS.md').write_text('Keep upstream workspace instructions.')
    runtime = root / 'runtime'
    entrypoint = runtime / 'node_modules/openclaw/openclaw.mjs'
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text('fixture, never executed')
    node, docker = root / 'node', root / 'docker'
    for path in (node, docker):
        path.write_text('fixture, never executed')
        path.chmod(0o700)
    source = root / 'source'
    wrapper = source / 'extensions/services/pixel-agent/host/cancellable-exec.sh'
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text('#!/bin/sh\nexit 0\n')
    # Darwin's sockaddr_un length is shorter than pytest's usual temp paths.
    with tempfile.TemporaryDirectory(prefix='ods-layout-', dir='/tmp') as short:
        with socket.socket(socket.AF_UNIX) as listener:
            docker_socket = Path(short).resolve() / 'docker.sock'
            listener.bind(str(docker_socket))
            yield dict(candidate=candidate, home=home, node=node, runtime=runtime, docker=docker,
                       docker_socket=docker_socket, ods_source=source, ingress_image='sha256:' + 'a' * 64,
                       compose_project='ods-fixture', ingress_gid=20)


def test_layout_preserves_candidate_and_generates_unloaded_private_template(inputs):
    before = (inputs['candidate'] / 'openclaw.json').read_bytes()
    result = layout.prepare(**inputs)
    home = inputs['home']
    assert result == home / 'gateway.plist'
    template = plistlib.loads(result.read_bytes())
    assert template['ProgramArguments'][:2] == ['/usr/bin/env', '-i']
    assert template['ProgramArguments'][-4:] == ['gateway', 'run', '--port', '18789']
    assert 'HOME=' + str(home) in template['ProgramArguments']
    assert 'PIXEL_HISTORY_TRANSPORT=docker-exec' in template['ProgramArguments']
    assert 'PIXEL_HISTORY_PROJECT=ods-fixture' in template['ProgramArguments']
    assert not any(item.startswith('PIXEL_INGRESS_SOCKET=') for item in template['ProgramArguments'])
    assert template['WorkingDirectory'] == str(home / '.openclaw/workspace-pixel')
    assert (home / 'gateway-template.sb').read_text() == '(version 1)\n(deny default)\n'
    assert '/usr/bin/env -u NODE_OPTIONS -u NODE_PATH' in (home / 'openclaw').read_text()
    assert json.loads((home / '.openclaw/openclaw.json').read_text()) == json.loads(before)
    assert (home / '.openclaw/openclaw.json').read_bytes() == before
    assert (inputs['candidate'] / 'openclaw.json').read_bytes() == before
    assert (home / '.openclaw/workspace-pixel/AGENTS.md').read_text() == 'Keep upstream workspace instructions.'
    assert (home / '.openclaw/.ods-exec-control/cancellable-exec.sh').stat().st_mode & 0o777 == 0o500
    for directory, _, files in os.walk(home):
        assert Path(directory).stat().st_mode & 0o777 == 0o700
        for name in files: assert (Path(directory) / name).stat().st_mode & 0o077 == 0
    assert not list(home.parent.glob('.pixel-layout-*'))


def test_layout_can_live_under_installed_ods_source(inputs):
    home = inputs['ods_source'] / 'data' / 'Native Home'
    home.parent.mkdir()
    inputs['home'] = home
    path = inputs['candidate'] / 'openclaw.json'
    value = json.loads(path.read_text())
    value['agents']['list'][0]['workspace'] = str(home / '.openclaw/workspace-pixel')
    value['agents']['defaults']['sandbox']['docker']['binds'] = [str(home / '.openclaw/.ods-exec-control') + ':/run/pixel-ods-control:ro']
    path.write_text(json.dumps(value))
    assert layout.prepare(**inputs) == home / 'gateway.plist'


@pytest.mark.parametrize('fault', ['existing', 'home-symlink', 'workspace-symlink', 'wrong-home',
                                 'wrong-bind', 'not-socket', 'root', 'wrapper-symlink',
                                 'mutable-image', 'invalid-project', 'invalid-gid', 'migration'])
def test_layout_rejects_unsafe_inputs_before_publication(inputs, monkeypatch, fault):
    home, candidate = inputs['home'], inputs['candidate']
    if fault == 'migration': (candidate / 'migration.json').write_text('{}')
    if fault == 'existing': home.mkdir()
    if fault == 'home-symlink': home.symlink_to(candidate, target_is_directory=True)
    if fault == 'workspace-symlink':
        (candidate / 'workspace/link').symlink_to(candidate / 'workspace/AGENTS.md')
    if fault in ('wrong-home', 'wrong-bind'):
        path = candidate / 'openclaw.json'
        value = json.loads(path.read_text())
        if fault == 'wrong-home': value['agents']['list'][0]['workspace'] = '/other/workspace'
        else: value['agents']['defaults']['sandbox']['docker']['binds'] = ['/private:/host:rw']
        path.write_text(json.dumps(value))
    if fault == 'not-socket': inputs['docker_socket'] = inputs['node']
    if fault == 'root': monkeypatch.setattr(layout.os, 'geteuid', lambda: 0)
    if fault == 'mutable-image': inputs['ingress_image'] = 'node:latest'
    if fault == 'invalid-project': inputs['compose_project'] = 'other --all'
    if fault == 'invalid-gid': inputs['ingress_gid'] = -1
    if fault == 'wrapper-symlink':
        wrapper = inputs['ods_source'] / 'extensions/services/pixel-agent/host/cancellable-exec.sh'
        wrapper.unlink()
        wrapper.symlink_to(inputs['node'])
    with pytest.raises((ValueError, OSError)): layout.prepare(**inputs)
    assert os.path.lexists(home) is (fault in ('existing', 'home-symlink'))
    assert not list(home.parent.glob('.pixel-layout-*'))
