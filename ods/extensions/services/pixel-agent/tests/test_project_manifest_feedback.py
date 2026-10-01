"""Malformed captured dependencies give bounded guidance before job creation."""
import json
from pathlib import Path
import sys
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from project_controller import ProjectController
import test_project_transport as transport_tests


def test_python_65_digit_hash_gets_exact_guidance_then_corrected_request_can_submit(tmp_path):
    project = tmp_path / 'demo'
    project.mkdir()
    (project / 'ods-project.json').write_text('{"runtime":"python"}')
    (project / 'main.py').write_text('print("fixture")')
    (project / 'tests').mkdir()
    (project / 'tests/test_main.py').write_text('import unittest\n')
    lock = project / 'requirements.lock'
    lock.write_text('Pillow==11.0.0 --hash=sha256:' + 'a' * 65)
    controller = ProjectController(tmp_path, tmp_path / 'state', 'sha256:' + 'b' * 64,
                                   python_image='sha256:' + 'c' * 64, authorize=lambda *_: True)
    envelope = {'context': {'sessionId': 'owner', 'toolCallId': 'malformed'},
        'request': {'schemaVersion': 1, 'action': 'submit', 'project': 'demo', 'outputDirectory': 'out'}}
    exchange = transport_tests.ProjectTransportTests().exchange
    try:
        with patch.object(controller.pool, 'submit', return_value=Mock()) as execute:
            result = exchange(controller, envelope)
            assert result['status'] == 'invalid-request'
            assert result['executionStarted'] is False
            assert result['issue'] == {'code': 'python-lock-sha256', 'file': 'requirements.lock'}
            assert 'a' * 65 not in json.dumps(result)
            assert lock.read_text().endswith('a' * 65)  # No automatic hash editing.
            execute.assert_not_called()
            with controller.jobs._connect() as db:
                assert db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 0
            controller.authorize = lambda *_: False
            assert exchange(controller, envelope) == {'schemaVersion': 1, 'kind': 'ods-project-job', 'status': 'denied'}
            controller.authorize = lambda *_: True
            lock.write_text('Pillow==11.0.0 --hash=sha256:' + 'a' * 64)
            envelope['context']['toolCallId'] = 'corrected'
            assert exchange(controller, envelope)['status'] == 'queued'
            execute.assert_called_once()
    finally:
        controller.close()
