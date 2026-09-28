"""Verify the library's image selection with Docker's actual Compose client."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest
import yaml

ODS = Path(__file__).resolve().parents[1]
LIBRARY = ODS / 'extensions/library/services'
PYTHON_IMAGE = 'python:3.11-slim@sha256:e41613d42d4891e4930f79523f93f81bbc7632584ec65e36ab055f41a800b41e'


def docker(*args, timeout=60):
    return subprocess.run(['docker', *args], check=True, capture_output=True,
                          text=True, encoding='utf-8', timeout=timeout).stdout


@pytest.mark.skipif(shutil.which('docker') is None, reason='Docker Compose CLI required; no daemon needed')
@pytest.mark.parametrize('backend, suffix', [('cpu', 'cpu'), ('amd', 'rocm'), ('nvidia', 'cuda')])
def test_compose_selects_invokeai_backend(backend, suffix):
    recipe = LIBRARY / 'invokeai'
    args = ['compose', '-f', str(recipe / 'compose.yaml')]
    if backend != 'cpu':
        args += ['-f', str(recipe / f'compose.{backend}.yaml')]
    config = json.loads(docker(*args, 'config', '--format', 'json'))
    service = config['services']['invokeai']
    assert service['image'].startswith(f'ghcr.io/invoke-ai/invokeai:v6.11.1-{suffix}@sha256:')
    assert '/api/v1/app/version' in service['healthcheck']['test'][-1]
    assert service['ports'][0]['host_ip'] == '127.0.0.1'
    if backend == 'amd':
        assert service['environment']['RENDER_GROUP_ID']
        assert {device['source'] for device in service['devices']} == {'/dev/dri', '/dev/kfd'}
    elif backend == 'nvidia':
        assert service['deploy']['resources']['reservations']['devices'][0]['driver'] == 'nvidia'


@pytest.mark.skipif(os.environ.get('ODS_RUN_DOCKER_SECURITY_TESTS') != '1',
                    reason='Requires an explicitly selected disposable Docker test host')
def test_local_library_policy_rebuilds_an_existing_untrusted_tag(tmp_path):
    # Use the shipped policy, not an independently invented test setting.
    recipe = yaml.safe_load((LIBRARY / 'audiobookshelf/compose.yaml').read_text())
    policy = recipe['services']['audiobookshelf']['pull_policy']
    project = 'ods-build-policy-' + uuid.uuid4().hex[:12]
    image = 'ods/' + project + ':test'
    container_name = project + '-app'
    dockerfile = tmp_path / 'Dockerfile'
    dockerfile.write_text(f'FROM {PYTHON_IMAGE}\nENV ODS_BUILD_ORIGIN=unreviewed\n', encoding='utf-8')
    original_id = None
    try:
        docker('build', '-t', image, str(tmp_path), timeout=300)
        original_id = docker('image', 'inspect', '--format', '{{.Id}}', image).strip()
        dockerfile.write_text(f'FROM {PYTHON_IMAGE}\nENV ODS_BUILD_ORIGIN=reviewed\n', encoding='utf-8')
        compose = tmp_path / 'compose.yaml'
        compose.write_text(yaml.safe_dump({'services': {'app': {
            'image': image, 'pull_policy': policy, 'build': {'context': '.'},
            'container_name': container_name,
            'command': ['python', '-c', 'import time; time.sleep(300)'],
            'network_mode': 'none', 'read_only': True, 'cap_drop': ['ALL'],
            'security_opt': ['no-new-privileges:true'],
        }}}), encoding='utf-8')
        docker('compose', '-p', project, '-f', str(compose), 'up', '-d', timeout=300)
        container = json.loads(docker('inspect', container_name))[0]
        assert container['State']['Running']
        assert container['Image'] != original_id
        assert 'ODS_BUILD_ORIGIN=reviewed' in container['Config']['Env']
        assert 'ODS_BUILD_ORIGIN=unreviewed' not in container['Config']['Env']
    finally:
        # These random identities belong only to this fixture, never ODS itself.
        subprocess.run(['docker', 'rm', '-f', container_name], capture_output=True, timeout=30)
        subprocess.run(['docker', 'image', 'rm', image], capture_output=True, timeout=30)
        if original_id:
            subprocess.run(['docker', 'image', 'rm', original_id], capture_output=True, timeout=30)
