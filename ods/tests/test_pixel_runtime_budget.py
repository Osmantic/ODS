"""The native and Linux installers share one unpublished runtime overlay."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


WRITER = Path(__file__).resolve().parents[1] / 'installers/lib/pixel-runtime-budget.py'


def configuration(context=16384):
    return {
        'models': {'providers': {'ods-local': {
            'api': 'openai-completions', 'apiKey': 'local-no-auth',
            'baseUrl': 'http://127.0.0.1:11434/v1',
            'models': [{'id': 'Qwen3.5-9B', 'name': 'ODS Local Qwen3.5-9B',
                        'contextWindow': context, 'maxTokens': 4096, 'reasoning': False}]}}},
        'agents': {'defaults': {}, 'list': [{'id': 'pixel', 'model': 'ods-local/Qwen3.5-9B'}]},
        'session': {}, 'tools': {'alsoAllow': ['pixel_ops_run'],
                               'sandbox': {'tools': {'allow': ['pixel_ops_run']}}},
    }


def invoke(path, home, explicit_home=None):
    args = [sys.executable, str(WRITER), str(path), '3099', '']
    if explicit_home is not None:
        args.append(str(explicit_home))
    return subprocess.run(args, env={**os.environ, 'HOME': str(home)},
                          capture_output=True, text=True, timeout=10)


@pytest.mark.parametrize('context', [8192, 16384, 32768, 65536])
def test_shared_overlay_is_staged_idempotent_and_uses_selected_home(tmp_path, context):
    tmp_path.chmod(0o700)
    path = tmp_path / 'openclaw.json'
    before = json.dumps(configuration(context))
    path.write_text(before)
    path.chmod(0o600)
    owner_home = tmp_path / 'Owner Home'
    openclaw_home = owner_home / '.openclaw'
    result = invoke(path, tmp_path / 'isolated-validator-home', openclaw_home)
    assert result.returncode == 0, result.stderr
    staged = Path(result.stdout.strip())
    assert staged.parent == path.parent
    assert staged.stat().st_mode & 0o777 == 0o600
    assert path.read_text() == before
    value = json.loads(staged.read_text())
    assert value["agents"]["defaults"]["compaction"]["timeoutSeconds"] == 1800
    assert value['agents']['defaults']['sandbox']['docker']['binds'] == [
        str(openclaw_home / '.ods-exec-control') + ':/run/pixel-ods-control:ro']
    agent = value['agents']['list'][0]
    assert agent['experimental']['localModelLean'] is False
    assert value['tools']['toolSearch']['enabled'] is True
    assert value['tools']['toolSearch']['mode'] == 'tools'
    assert agent['contextLimits']['toolResultMaxChars'] == max(4000, min(16000, context // 4))
    assert {'pixel_ops_run', 'pixel_ods_workspace_preview', 'create_goal'}.issubset(value['tools']['alsoAllow'])
    assert 'pixel_ods_extension_proposal' in value['tools']['alsoAllow']
    assert 'pixel_ods_extension_proposal' in value['tools']['sandbox']['tools']['allow']
    assert 'pixel_ods_extension_proposal' not in agent['tools']['deny']
    for tool in ('pixel_ods_skill', 'pixel_ods_python_library_proposal', 'pixel_ods_extension_request_status', 'pixel_ods_extension_request_prepare', 'pixel_ods_extension_request_advance'):
        assert tool in value['tools']['alsoAllow']
        assert tool in value['tools']['sandbox']['tools']['allow']
        assert tool not in agent['tools']['deny']
    assert value['plugins']['entries']['pixel-ods']['config']['perplexicaPort'] == 3099
    repeated = invoke(staged, owner_home, openclaw_home)
    assert repeated.returncode == 0, repeated.stderr
    assert repeated.stdout.strip() == 'unchanged'
    # Linux's implicit HOME and macOS's explicit OpenClaw home must agree.
    linux_result = invoke(path, owner_home)
    assert linux_result.returncode == 0, linux_result.stderr
    assert json.loads(Path(linux_result.stdout.strip()).read_text()) == value


@pytest.mark.parametrize('fault', ['relative-home', 'traversal-home', 'public', 'symlink', 'foreign-bind'])
def test_overlay_rejects_unsafe_input_without_modifying_config(tmp_path, fault):
    tmp_path.chmod(0o700)
    path = tmp_path / 'openclaw.json'
    value = configuration()
    if fault == 'foreign-bind':
        value['agents']['defaults']['sandbox'] = {'docker': {'binds': ['/private:/host:rw']}}
    path.write_text(json.dumps(value))
    path.chmod(0o644 if fault == 'public' else 0o600)
    if fault == 'symlink':
        actual = tmp_path / 'actual.json'
        path.rename(actual)
        path.symlink_to(actual)
    before = path.read_bytes()
    home = {'relative-home': Path('relative'), 'traversal-home': tmp_path / '..' / 'home'}.get(fault, tmp_path / 'home')
    result = invoke(path, tmp_path, home)
    assert result.returncode != 0
    assert path.read_bytes() == before
    assert not list(tmp_path.glob('.ods-pixel-runtime-budget.*'))
