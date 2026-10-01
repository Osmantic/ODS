"""The real installer build must survive an owner-private source context."""
import importlib.util
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / 'extensions/services/pixel-agent/host'
SPEC = importlib.util.spec_from_file_location(
    'inspection_permissions_install', ROOT / 'installers/lib/pixel-preview-inspection.py')
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)
sys.path.insert(0, str(HOST))
broker = importlib.import_module('preview_inspection')
protocol = importlib.import_module('preview_inspection_protocol')


def test_capsule_copy_permissions_are_explicit():
    dockerfile = (HOST / 'Dockerfile.inspection').read_text()
    assert 'COPY --chmod=0444 preview-inspection.requirements.lock /source/requirements.lock' in dockerfile
    assert 'COPY --chmod=0444 preview_inspection_protocol.py preview_inspection_capsule.py /source/' in dockerfile
    assert 'USER 65534:65534' in dockerfile


@pytest.mark.skipif(os.environ.get('ODS_INSPECTION_BUILD_TESTS') != '1',
                    reason='actual installer Docker build is opt in')
def test_private_installer_context_runs_as_nonroot():
    """No mocked build: record modes, then run the actual fixed installer command."""
    if sys.platform != 'linux' or os.getuid() == 0:
        pytest.skip('exercise as an ordinary Linux/WSL install owner')
    real_run = subprocess.run
    staged_modes = []

    def recording_run(argv, **kwargs):
        if 'build' in argv:
            context = Path(argv[-1])
            assert context.stat().st_mode & 0o777 == 0o700
            staged_modes.extend((p.name, p.stat().st_mode & 0o777) for p in context.iterdir())
        return real_run(argv, **kwargs)

    old_umask = os.umask(0o077)
    try:
        with tempfile.TemporaryDirectory(prefix='ods-inspection-private-source-') as temporary:
            source = Path(temporary)
            for name in installer.BUILD_FILES:
                (source / name).write_bytes((HOST / name).read_bytes())
            originals = {p.name: (p.read_bytes(), p.stat().st_mode & 0o777) for p in source.iterdir()}
            assert all(mode == 0o600 for _, mode in originals.values())
            with patch.object(installer.subprocess, 'run', side_effect=recording_run):
                config = installer.build_config(source=source, owner_uid=os.getuid(), transport='local')
            assert {p.name: (p.read_bytes(), p.stat().st_mode & 0o777) for p in source.iterdir()} == originals
    finally:
        os.umask(old_umask)
    assert dict(staged_modes) == dict.fromkeys(installer.BUILD_FILES, 0o600)

    # Reuse the production capsule restrictions, replacing only the entrypoint
    # arguments for a first non-root import/readability check. No host mounts.
    import uuid
    name = 'ods-inspection-permission-test-' + uuid.uuid4().hex
    argv = broker.capsule_argv(config, name)
    try:
        check = real_run(argv[:-1] + ['-c',
            'import os,stat,preview_inspection_protocol,preview_inspection_capsule; '
            'from pathlib import Path; assert os.getuid()==65534; '
            'files=["requirements.lock","preview_inspection_protocol.py","preview_inspection_capsule.py"]; '
            'assert all(stat.S_IMODE(Path("/source",f).stat().st_mode)==0o444 for f in files); '
            'assert all(not os.access(Path("/source",f),os.W_OK) for f in files); print("imports-readable-nonroot")'],
            input=b'', capture_output=True, timeout=45)
        assert check.returncode == 0, check.stderr.decode(errors='replace')
        assert check.stdout.strip() == b'imports-readable-nonroot'
    finally:
        real_run([*broker.docker_prefix(config), 'rm', '-f', name], capture_output=True, timeout=10)

    import workspace_preview as publisher
    with tempfile.TemporaryDirectory(prefix='ods-inspection-private-preview-') as temporary:
        root = Path(temporary)
        workspace, snapshots = root / 'workspace', root / 'snapshots'
        workspace.mkdir(mode=0o700)
        snapshots.mkdir(mode=0o700)
        site = workspace / 'site'
        site.mkdir(mode=0o700)
        index = site / 'index.html'
        index.write_text('<!doctype html><p id="result">Ready</p>')
        index.chmod(0o600)
        published = publisher.publish_snapshot(workspace, snapshots, 'site', os.getuid())
        request = dict(schemaVersion=1, action='inspect', siteId=published['siteId'],
                       sha256=published['sha256'], viewport={'width': 800, 'height': 600},
                       steps=[{'action': 'assert-text', 'locator': {'selector': '#result'}, 'expectedText': 'Ready'}])
        config['snapshotRoot'] = str(snapshots)
        result = broker.inspect_request(request, config)
        assert result['status'] == 'passed', json.dumps(result)
        assert result['planSha256'] == protocol.plan_hash(request)
        assert result['steps'][0]['before']['text']['actual'] == 'Ready'
