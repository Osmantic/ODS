import importlib.util
import ast
import os
from pathlib import Path
import pwd
import json
import subprocess

import pytest

HELPER = Path(__file__).resolve().parents[1] / 'installers/lib/pixel-project-runtime.py'
spec = importlib.util.spec_from_file_location('project_install', HELPER)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def config():
    owner = pwd.getpwuid(os.getuid())
    return {'ownerUid': owner.pw_uid, 'imageId': 'sha256:' + 'a' * 64,
            'workspace': str(Path(owner.pw_dir) / '.openclaw/workspace-pixel')}


def test_runtime_distribution_includes_capability_probe_and_all_local_imports():
    source = HELPER.parents[2] / 'extensions/services/pixel-agent/host'
    assert 'project_capabilities.py' in installer.FILES
    for name in installer.FILES:
        path = source / name
        assert path.is_file()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module:
                local = node.module.split('.')[0] + '.py'
                if (source / local).is_file():
                    assert local in installer.FILES, f'{name} depends on missing {local}'


def test_unit_runs_as_owner_and_never_enables_full_access():
    unit = installer.unit_bytes(config()).decode()
    assert f'User={pwd.getpwuid(os.getuid()).pw_name}' in unit
    assert ' --image sha256:' + 'a' * 64 in unit
    assert '--state-root /var/lib/ods-pixel-project' in unit
    assert 'NoNewPrivileges=true' in unit
    assert 'SupplementaryGroups=' not in unit
    assert 'sudo' not in unit
    assert 'enable_full_access' not in unit


@pytest.mark.parametrize('change', [{'imageId': 'node:latest'}, {'pythonImageId': 'python:latest'},
                                  {'pythonImageId': None}, {'ownerUid': 0},
                                  {'workspace': '/tmp/other-project'}, {'extra': True}])
def test_invalid_deployment_is_rejected(change):
    with pytest.raises(ValueError):
        installer.unit_bytes({**config(), **change})


def test_upgrade_keeps_legacy_unit_identity_and_adds_configured_python_image():
    legacy = config()
    assert '--python-image' not in installer.unit_bytes(legacy).decode()
    upgraded = {**legacy, 'pythonImageId': 'sha256:' + 'b' * 64}
    assert ' --python-image sha256:' + 'b' * 64 in installer.unit_bytes(upgraded).decode()
    assert installer.validate_config(legacy) == installer.validate_config(upgraded)


def test_build_includes_both_reviewed_images_and_no_project_files(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'Dockerfile.project-node').write_bytes(b'node definition')
    (source / 'Dockerfile.project-python').write_bytes(b'python definition')
    (source / 'untrusted.py').write_bytes(b'not in build context')
    definitions = []

    def build(argv, **kwargs):
        context = Path(argv[-1])
        assert set(path.name for path in context.iterdir()) == {'Dockerfile'}
        definitions.append((context / 'Dockerfile').read_bytes())
        Path(argv[argv.index('--iidfile') + 1]).write_text('sha256:' + ('a' if len(definitions) == 1 else 'b') * 64)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(installer.subprocess, 'run', build)
    result = installer.build_config(source, os.getuid())
    assert definitions == [b'node definition', b'python definition']
    assert result['imageId'] != result['pythonImageId']
    installer.validate_config(result)


@pytest.mark.skipif(os.environ.get('ODS_TEST_PROJECT_NODE') != '1', reason='real Docker build opt-in')
def test_fixed_runtime_build_returns_immutable_configuration(tmp_path):
    source = HELPER.parents[2] / 'extensions/services/pixel-agent/host'
    for name in ('Dockerfile.project-node', 'Dockerfile.project-python'):
        (tmp_path / name).write_bytes((source / name).read_bytes())
        (tmp_path / name).chmod(0o600)
    result = installer.build_config(tmp_path, os.getuid())
    assert result['imageId'].startswith('sha256:')
    assert len(result['imageId']) == 71
    assert len(result['pythonImageId']) == 71
    installer.validate_config(result)


@pytest.mark.parametrize('failure', ['modified', 'active', 'owner', None])
def test_cleanup_refuses_without_removing_any_file(tmp_path, monkeypatch, failure):
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    source = tmp_path / 'source'
    source.mkdir()
    unit = tmp_path / 'unit.service'
    receipt = tmp_path / 'config.json'
    deployment = config()
    for name in installer.FILES:
        (source / name).write_text('# reviewed source\n')
        (runtime / name).write_text('# reviewed source\n')
    monkeypatch.setattr(installer, 'PROGRAM_ROOT', runtime)
    monkeypatch.setattr(installer, 'UNIT', unit)
    monkeypatch.setattr(installer, 'CONFIG', receipt)
    unit.write_bytes(installer.unit_bytes(deployment))
    receipt.write_text(json.dumps(deployment, sort_keys=True) + '\n')
    if failure == 'modified':
        (runtime / installer.FILES[-1]).write_text('# not our file\n')
    original = {path: path.read_bytes() for path in [receipt, unit, *runtime.iterdir()]}
    history = tmp_path / 'jobs.sqlite3'
    history.write_bytes(b'preserved job history')
    monkeypatch.setattr(installer.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(installer.common, 'protected_parent', lambda *args, **kwargs: None)
    monkeypatch.setattr(installer.common, 'protected_file', lambda path: path.read_bytes())
    monkeypatch.setattr(installer.subprocess, 'run', lambda *args, **kwargs: subprocess.CompletedProcess([], 0 if failure == 'active' else 3))
    uid = deployment['ownerUid'] + 1 if failure == 'owner' else deployment['ownerUid']
    if failure:
        with pytest.raises(ValueError):
            installer.cleanup_linux(source, uid, remove=True)
        assert all(path.read_bytes() == body for path, body in original.items())
    else:
        removed = installer.cleanup_linux(source, uid, remove=True)
        assert set(removed) == {str(path) for path in original}
        assert all(not path.exists() for path in original)
        assert not runtime.exists()
    assert history.read_bytes() == b'preserved job history'
