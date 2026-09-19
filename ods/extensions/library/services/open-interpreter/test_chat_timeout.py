"""The public chat endpoint reports its worker deadline and removes its script."""
import importlib.util
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize('outcome,status', [('timeout', 504), ('failure', 500), ('success', 200)])
def test_chat_worker_outcomes_and_cleanup(monkeypatch, outcome, status):
    monkeypatch.setenv('OPEN_INTERPRETER_API_KEY', 'timeout-test-key')
    spec = importlib.util.spec_from_file_location('interpreter_timeout_server', Path(__file__).with_name('server.py'))
    server = importlib.util.module_from_spec(spec)
    mkdir = Path.mkdir
    with monkeypatch.context() as context:
        context.setattr(Path, 'mkdir', lambda p, *a, **k: None if p == Path('/app/data') else mkdir(p, *a, **k))
        spec.loader.exec_module(server)
    scripts = []

    def run(command, **kwargs):
        script = Path(command[1])
        assert script.is_file()
        assert kwargs['timeout'] == 300
        scripts.append(script)
        if outcome == 'timeout':
            raise subprocess.TimeoutExpired(command, 300, output='private partial output')
        return subprocess.CompletedProcess(command, 1 if outcome == 'failure' else 0, 'RESULT: done', 'private stderr')

    monkeypatch.setattr(server.subprocess, 'run', run)
    response = TestClient(server.app, raise_server_exceptions=False).post(
        '/chat', json={'message': 'Run my analysis'},
        headers={'Authorization': 'Bearer timeout-test-key'},
    )
    assert response.status_code == status
    assert len(scripts) == 1 and not scripts[0].exists()
    assert 'private' not in response.text
    if outcome == 'timeout':
        assert response.json() == {'detail': 'Interpreter execution timed out'}
