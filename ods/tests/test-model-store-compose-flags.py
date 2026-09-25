"""Saved stacks gain only the registered model mounts; no Docker is called."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('store_flags', ROOT/'scripts/model-store-compose-flags.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def install(tmp_path):
    root = tmp_path/'installation'
    (root/'data/models').mkdir(parents=True)
    ssd = tmp_path/'External SSD'
    ssd.mkdir()
    (root/'.env').write_text('ODS_ACTIVE_MODEL_STORE=ssd\n')
    registry = {'schemaVersion': 1, 'stores': [{'id': 'ssd', 'hostPath': str(ssd), 'containerPath': '/model-stores/ssd'}]}
    (root/'data/model-stores.json').write_text(json.dumps(registry))
    overlay = {'services': {'dashboard-api': {'volumes': [{'type': 'bind', 'source': str(ssd),
        'target': '/model-stores/ssd', 'read_only': True, 'bind': {'create_host_path': False}}]}}}
    (root/'.model-stores.compose.json').write_text(json.dumps(overlay))
    return root


def test_old_saved_stack_adds_registered_and_active_mount_without_rewriting_user_flags(install):
    original = ['--env-file', '.env', '-p', 'owner-stack', '-f', 'base.yml', '-f', 'custom.yml', '--profile', 'owner']
    saved = install/'.compose-flags'
    saved.write_text(' '.join(original))
    result = module.resolve_flags(install, original)
    assert result == original+['-f', '.model-stores.compose.json', '-f', 'data/.active-model-store.compose.json']
    assert saved.read_text() == ' '.join(original)
    active = json.loads((install/'data/.active-model-store.compose.json').read_text())
    assert active['services']['llama-server']['volumes'][0]['target'] == '/models'
    assert active['services']['llama-server']['volumes'][0]['read_only'] is True


def test_reset_default_removes_only_generated_active_overlay_and_deduplicates(install):
    (install/'.env').write_text('ODS_ACTIVE_MODEL_STORE=default\n')
    flags = ['-f', 'base.yml', '-f', str(install/'.model-stores.compose.json'),
        '--file=data/.active-model-store.compose.json', '-f', 'owner/.active-model-store.compose.json']
    assert module.resolve_flags(install, flags) == ['-f', 'base.yml', '-f', 'owner/.active-model-store.compose.json',
        '-f', '.model-stores.compose.json']


def test_missing_overlay_or_altered_mount_fails_closed(install):
    overlay = install/'.model-stores.compose.json'
    content = json.loads(overlay.read_text())
    content['services']['dashboard-api']['volumes'][0]['read_only'] = False
    overlay.write_text(json.dumps(content))
    with pytest.raises(ValueError, match='does not match'): module.resolve_flags(install, ['-f', 'base.yml'])
    overlay.unlink()
    with pytest.raises(ValueError, match='no matching Compose overlay'): module.resolve_flags(install, ['-f', 'base.yml'])


def test_unregistered_install_keeps_arguments_exactly(tmp_path):
    original = ['-f', 'user stack.yml', '--env-file', 'owner.env']
    assert module.resolve_flags(tmp_path, original) == original


@pytest.mark.skipif(sys.platform == 'win32', reason='macOS shell path runs under POSIX')
def test_macos_reads_saved_flags_created_before_registration(install):
    original = '-f base.yml -f custom.yml'
    (install/'.compose-flags').write_text(original)
    (install/'scripts').mkdir()
    # Forward only the helper's path; imports remain in the unchanged checkout.
    (install/'scripts/model-store-compose-flags.py').symlink_to(ROOT/'scripts/model-store-compose-flags.py')
    cli = (ROOT/'installers/macos/ods-macos.sh').read_text()
    function = 'get_compose_flags() {'+cli.split('get_compose_flags() {', 1)[1].split('\ncompose_pull_with_retry()', 1)[0]
    script = 'set -e\nsource "$1"\nINSTALL_DIR="$2"\nensure_hermes_dashboard_session_token() { :; }\n'+function+'\nget_compose_flags\n'
    result = subprocess.run(['bash', '-s', '--', str(ROOT/'installers/macos/lib/native-model.sh'), str(install)],
        input=script, text=True, capture_output=True, check=True)
    assert result.stdout.strip() == original+' -f .model-stores.compose.json -f data/.active-model-store.compose.json'
    assert (install/'.compose-flags').read_text() == original
