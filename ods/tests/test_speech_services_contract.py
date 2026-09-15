"""Contract validation and network isolation for speech services (Whisper & TTS).

Run live docker test: ODS_TEST_SPEECH_DOCKER=1 pytest -q tests/test_speech_services_contract.py
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WHISPER_DIR = ROOT / 'extensions/services/whisper'
TTS_DIR = ROOT / 'extensions/services/tts'


def test_whisper_compose_and_catalog_contract():
    catalog = json.loads((ROOT / 'config/extensions-catalog.json').read_text())
    entry = next((item for item in catalog['extensions'] if item['id'] == 'whisper'), None)
    assert entry is not None
    assert entry['port'] == 8000
    assert entry['external_port_default'] == 9000

    compose = yaml.safe_load((WHISPER_DIR / 'compose.yaml').read_text())
    assert 'whisper' in compose['services']
    service_def = compose['services']['whisper']

    # Network isolation: must attach to ods-network
    assert service_def.get('networks') == ['ods-network']
    assert compose['networks']['ods-network']['external'] is True

    # Security options
    assert service_def.get('security_opt') == ['no-new-privileges:true']

    # Port mapping check
    ports = service_def['ports']
    assert any('WHISPER_PORT:-9000}:8000' in p for p in ports)


def test_tts_compose_and_catalog_contract():
    catalog = json.loads((ROOT / 'config/extensions-catalog.json').read_text())
    entry = next((item for item in catalog['extensions'] if item['id'] == 'tts'), None)
    assert entry is not None
    assert entry['port'] == 8880
    assert entry['external_port_default'] == 8880

    compose = yaml.safe_load((TTS_DIR / 'compose.yaml').read_text())
    assert 'tts' in compose['services']
    service_def = compose['services']['tts']

    # Network isolation: must attach to ods-network
    assert service_def.get('networks') == ['ods-network']
    assert compose['networks']['ods-network']['external'] is True

    # Security options
    assert service_def.get('security_opt') == ['no-new-privileges:true']

    # Port mapping check
    ports = service_def['ports']
    assert any('TTS_PORT:-8880}:8880' in p for p in ports)


@pytest.mark.skipif(os.environ.get('ODS_TEST_SPEECH_DOCKER') != '1', reason='Opt-in Docker lifecycle test')
def test_speech_services_docker_config(tmp_path):
    for service_name, dir_path in [('whisper', WHISPER_DIR), ('tts', TTS_DIR)]:
        command = ['docker', 'compose', '--project-directory', str(tmp_path), '-f', str(dir_path / 'compose.yaml'), 'config']
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0
