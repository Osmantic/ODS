"""The shipped Chroma image must be able to execute its HTTP health probe."""
import json
import os
from pathlib import Path
import subprocess
import uuid

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / 'extensions/library/services/chromadb/compose.yaml'


def test_healthcheck_uses_image_tools_and_checks_http_status():
    probe = yaml.safe_load(COMPOSE.read_text(encoding='utf-8'))['services']['chromadb']['healthcheck']['test']
    assert probe[:5] == ['CMD', 'timeout', '8', 'bash', '-ec']
    assert 'python' not in probe[-1] and 'curl' not in probe[-1]
    assert 'GET /api/v2/heartbeat HTTP/1.1' in probe[-1]
    assert '$$status == 200' in probe[-1]


@pytest.mark.skipif(os.environ.get('ODS_TEST_CHROMADB_DOCKER') != '1', reason='Opt-in Docker HTTP probe')
def test_image_probe_accepts_heartbeat_but_rejects_404_and_closed_port(tmp_path):
    name = 'ods-chroma-probe-' + uuid.uuid4().hex[:10]
    overlay = tmp_path / 'isolation.yaml'
    overlay.write_text(f'''services:
  chromadb:
    container_name: {name}
    ports: !override ["127.0.0.1:0:8000"]
networks:
  ods-network:
    name: {name}
''', encoding='utf-8')
    command = ['docker', 'compose', '--project-name', name, '--project-directory', str(tmp_path), '-f', str(COMPOSE), '-f', str(overlay)]

    def run(args, check=True):
        return subprocess.run(args, check=check, capture_output=True, text=True, timeout=120)

    plan = json.loads(run([*command, 'config', '--format', 'json']).stdout)['services']['chromadb']
    assert plan['ports'][0]['host_ip'] == '127.0.0.1'
    run(['docker', 'network', 'create', name])
    try:
        run([*command, 'up', '-d', '--wait', '--wait-timeout', '90'])
        # `compose config` escapes dollars for round-tripping; inspect the
        # actual engine command rather than executing that representation.
        probe = json.loads(run(['docker', 'inspect', '--format', '{{json .Config.Healthcheck.Test}}', name]).stdout)[1:]
        assert '$status' in probe[-1] and '$$status' not in probe[-1]
        run(['docker', 'exec', name, *probe])
        for source, replacement in [('/api/v2/heartbeat', '/api/v2/missing-fixture'), ('/127.0.0.1/8000', '/127.0.0.1/1')]:
            changed = [*probe[:-1], probe[-1].replace(source, replacement)]
            assert run(['docker', 'exec', name, *changed], check=False).returncode != 0
    finally:
        run([*command, 'down', '--timeout', '10'])
        run(['docker', 'network', 'rm', name])
