"""The generated agent persona must not mistake a sidecar for its application."""
import importlib.util
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/build-installation-context.py'
spec = importlib.util.spec_from_file_location('persona', SCRIPT)
persona = importlib.util.module_from_spec(spec)
spec.loader.exec_module(persona)


@pytest.mark.parametrize('profile', ['full', 'local-lemonade'])
@pytest.mark.parametrize('primary_up', [False, True])
def test_cli_lists_application_only_when_its_manifest_container_runs(
    tmp_path, monkeypatch, capsys, profile, primary_up,
):
    env = tmp_path / '.env'
    env.write_text('ODS_DEVICE_NAME=test-ods\n', encoding='utf-8')
    services = tmp_path / 'extensions/services'
    for service in ['langfuse', 'open-webui']:
        dest = services / service
        dest.mkdir(parents=True)
        source = SCRIPT.parents[1] / 'extensions/services' / service / 'manifest.yaml'
        (dest / 'manifest.yaml').write_text(source.read_text(), encoding='utf-8')

    names = 'ods-langfuse-postgres\nods-langfuse-worker\nods-webui\nods-unrelated\n'
    if primary_up:
        names += 'ods-langfuse-web\n'

    def docker_ps(command, **kwargs):
        assert command[:2] == ['docker', 'ps']
        return subprocess.CompletedProcess(command, 0, names, '')

    monkeypatch.setattr(persona.subprocess, 'run', docker_ps)
    monkeypatch.setattr(persona, '_loaded_model', lambda **_kwargs: None)
    assert persona.main(['--env', str(env), '--check', '--profile', profile]) == 0
    output = capsys.readouterr().out
    assert ('Langfuse' in output) is primary_up
    assert 'Open WebUI' in output
    assert 'unrelated' not in output
