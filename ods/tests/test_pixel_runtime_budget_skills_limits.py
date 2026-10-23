"""ODS-owned skillsLimits.maxSkillsPromptChars profile for the Pixel agent."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


WRITER = Path(__file__).resolve().parents[1] / 'installers/lib/pixel-runtime-budget.py'


def configuration(context=8192):
    return {
        'models': {'providers': {'ods-local': {
            'api': 'openai-completions', 'apiKey': 'local-no-auth',
            'baseUrl': 'http://127.0.0.1:11434/v1',
            'models': [{'id': 'Qwen3.5-9B', 'name': 'ODS Local Qwen3.5-9B',
                        'contextWindow': context, 'maxTokens': 2048, 'reasoning': False}]}}},
        'agents': {'defaults': {}, 'list': [{'id': 'pixel', 'model': 'ods-local/Qwen3.5-9B'}]},
        'session': {}, 'tools': {'alsoAllow': [], 'sandbox': {'tools': {'allow': []}}},
    }


def invoke(path, home):
    return subprocess.run(
        [sys.executable, str(WRITER), str(path), '3099', '', str(home)],
        env={**os.environ, 'HOME': str(home)},
        capture_output=True, text=True, timeout=10,
    )


def stage(tmp_path, value):
    tmp_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp_path.chmod(0o700)
    path = tmp_path / 'openclaw.json'
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    result = invoke(path, tmp_path / '.openclaw')
    assert result.returncode == 0, result.stderr
    return json.loads(Path(result.stdout.strip()).read_text())


def pixel_agent(value):
    return next(item for item in value['agents']['list'] if item.get('id') == 'pixel')


@pytest.mark.parametrize('context', [4096, 8192])
def test_small_context_sets_managed_skills_prompt_chars(tmp_path, context):
    updated = stage(tmp_path, configuration(context))
    assert pixel_agent(updated)['skillsLimits']['maxSkillsPromptChars'] == 4300


def test_small_context_overrides_preexisting_stricter_value(tmp_path):
    value = configuration(8192)
    value['agents']['list'][0]['skillsLimits'] = {'maxSkillsPromptChars': 3500, 'maxSkillsInPrompt': 27}
    updated = stage(tmp_path, value)
    limits = pixel_agent(updated)['skillsLimits']
    assert limits['maxSkillsPromptChars'] == 4300
    assert limits['maxSkillsInPrompt'] == 27


@pytest.mark.parametrize('context', [32768, 65536])
def test_large_context_removes_only_managed_value(tmp_path, context):
    value = configuration(context)
    value['agents']['list'][0]['skillsLimits'] = {
        'maxSkillsPromptChars': 4300, 'maxSkillsInPrompt': 27,
    }
    updated = stage(tmp_path, value)
    limits = pixel_agent(updated)['skillsLimits']
    assert 'maxSkillsPromptChars' not in limits
    assert limits == {'maxSkillsInPrompt': 27}


def test_large_context_preserves_owner_stricter_value(tmp_path):
    value = configuration(65536)
    value['agents']['list'][0]['skillsLimits'] = {'maxSkillsPromptChars': 3500}
    updated = stage(tmp_path, value)
    assert pixel_agent(updated)['skillsLimits']['maxSkillsPromptChars'] == 3500


def test_large_context_drops_empty_skills_limits(tmp_path):
    value = configuration(65536)
    value['agents']['list'][0]['skillsLimits'] = {'maxSkillsPromptChars': 4300}
    updated = stage(tmp_path, value)
    assert 'skillsLimits' not in pixel_agent(updated)


def test_8k_to_64k_restore_removes_managed_value(tmp_path):
    small = stage(tmp_path, configuration(8192))
    assert pixel_agent(small)['skillsLimits']['maxSkillsPromptChars'] == 4300
    tmp_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp_path.chmod(0o700)
    path = tmp_path / 'openclaw.json'
    small['models']['providers']['ods-local']['models'][0]['contextWindow'] = 65536
    path.write_text(json.dumps(small))
    path.chmod(0o600)
    result = invoke(path, tmp_path / '.openclaw')
    assert result.returncode == 0, result.stderr
    restored = json.loads(Path(result.stdout.strip()).read_text())
    assert 'skillsLimits' not in pixel_agent(restored)


@pytest.mark.parametrize('bad', [None, 'string', 4300, ['x'], True])
def test_malformed_skills_limits_fails_closed_without_write(tmp_path, bad):
    tmp_path.chmod(0o700)
    value = configuration(8192)
    value['agents']['list'][0]['skillsLimits'] = bad
    path = tmp_path / 'openclaw.json'
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    before = path.read_bytes()
    result = invoke(path, tmp_path / '.openclaw')
    assert result.returncode != 0
    assert 'skillsLimits' in result.stderr
    assert path.read_bytes() == before
    assert not list(tmp_path.glob('.ods-pixel-runtime-budget.*'))


@pytest.mark.parametrize('context', [8192, 32768, 65536])
def test_activation_and_installer_share_skills_budget(tmp_path, context):
    sys.path.insert(0, str(WRITER.parents[2] / 'bin'))
    import pixel_model_contract as contract
    large = stage(tmp_path, configuration(65536))
    large['agents']['list'][0]['skillsLimits'] = {'maxSkillsInPrompt': 27, 'maxSkillsPromptChars': 16000}
    original = json.loads(json.dumps(large))
    proposed = contract.projection(large)['contract']
    proposed.update(contextLength=context, maxTokens=2048)
    planned = contract.plan(large, proposed)
    assert large == original
    expected = 4300 if context <= 8192 else 16000
    assert pixel_agent(planned)['skillsLimits'] == {'maxSkillsInPrompt': 27, 'maxSkillsPromptChars': expected}
    if context == 8192:
        proposed['contextLength'] = 65536
        restored = contract.plan(planned, proposed)
        assert pixel_agent(restored)['skillsLimits'] == {'maxSkillsInPrompt': 27}


@pytest.mark.parametrize('bad', [None, 'bad', [], 4300])
def test_activation_rejects_malformed_skills_limits_without_mutating_input(tmp_path, bad):
    sys.path.insert(0, str(WRITER.parents[2] / 'bin'))
    import pixel_model_contract as contract
    value = stage(tmp_path, configuration(65536))
    proposed = contract.projection(value)['contract']
    proposed['contextLength'] = 8192
    value['agents']['list'][0]['skillsLimits'] = bad
    before = json.loads(json.dumps(value))
    with pytest.raises(contract.ModelError, match='skills-limits'):
        contract.plan(value, proposed)
    assert value == before
