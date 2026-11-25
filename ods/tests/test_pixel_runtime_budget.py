"""The native and Linux installers share one unpublished runtime overlay."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


WRITER = Path(__file__).resolve().parents[1] / 'installers/lib/pixel-runtime-budget.py'
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import pixel_model_contract  # noqa: E402


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


@pytest.mark.parametrize('policy,expected', [('supported', ['text', 'image']),
    ('unknown', ['text', 'image']), ('unsupported', ['text'])])
def test_image_policy_overlay_binds_selected_route_and_replaces_previous_policy(tmp_path, policy, expected):
    tmp_path.chmod(0o700)
    value = configuration()
    value['plugins'] = {'entries': {'pixel-ods': {'config': {'modelImageInput': 'supported'}}}}
    value['models']['providers']['ods-local']['models'][0]['input'] = ['text', 'image']
    path = tmp_path / 'openclaw.json'
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    answers = tmp_path / 'onboarding.json'
    answers.write_text(json.dumps({'modelProvider': 'ods-local', 'modelId': 'Qwen3.5-9B',
        'modelName': 'ODS Local Qwen3.5-9B', 'modelImageInput': policy}))
    answers.chmod(0o600)
    args = [sys.executable, str(WRITER), str(path), '3099', str(answers), str(tmp_path / '.openclaw')]
    result = subprocess.run(args, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    updated = json.loads(Path(result.stdout.strip()).read_text())
    assert updated['models']['providers']['ods-local']['models'][0]['input'] == expected
    assert updated['plugins']['entries']['pixel-ods']['config']['modelImageInput'] == policy
    # A stale policy for another concrete model must not be applied at all.
    answers.write_text(json.dumps({'modelProvider': 'ods-local', 'modelId': 'different',
        'modelName': 'ODS Local different', 'modelImageInput': policy}))
    before = path.read_bytes()
    result = subprocess.run(args, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0 and 'route does not match' in result.stderr
    assert path.read_bytes() == before


@pytest.mark.parametrize('transport', [None, 'unix', 'native', 'unknown'])
def test_inspection_allowlists_require_explicit_provisioned_transport(tmp_path, transport):
    tmp_path.chmod(0o700)
    value = configuration()
    tool = 'pixel_ods_workspace_preview_inspect'
    value['tools']['alsoAllow'].append(tool)
    value['tools']['sandbox']['tools']['allow'].append(tool)
    value['agents']['list'][0]['tools'] = {'deny': [tool]}
    plugin = {}
    if transport is not None: plugin['workspacePreviewInspectionTransport'] = transport
    value['plugins'] = {'entries': {'pixel-ods': {'config': plugin}}}
    path = tmp_path / 'openclaw.json'
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    result = invoke(path, tmp_path)
    assert result.returncode == 0, result.stderr
    updated = json.loads(Path(result.stdout.strip()).read_text())
    enabled = transport in ('unix', 'native')
    assert (tool in updated['tools']['alsoAllow']) is enabled
    assert (tool in updated['tools']['sandbox']['tools']['allow']) is enabled
    assert (tool not in updated['agents']['list'][0]['tools']['deny']) is enabled


def test_fresh_installer_explicitly_stages_inspection_transport(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / 'openclaw.json'
    path.write_text(json.dumps(configuration()))
    path.chmod(0o600)
    result = subprocess.run([sys.executable, str(WRITER), str(path), '3099', '',
        str(tmp_path / '.openclaw'), 'unix'], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    updated = json.loads(Path(result.stdout.strip()).read_text())
    assert updated['plugins']['entries']['pixel-ods']['config']['workspacePreviewInspectionTransport'] == 'unix'
    assert 'pixel_ods_workspace_preview_inspect' in updated['tools']['alsoAllow']


@pytest.mark.parametrize('socket_path', [None, '/run/ods-project/control.sock', 'relative.sock'])
def test_project_allowlists_follow_optional_controller_config(tmp_path, socket_path):
    tmp_path.chmod(0o700)
    value = configuration()
    tool = 'pixel_ods_project_build'
    value['tools']['alsoAllow'].append(tool)
    value['tools']['sandbox']['tools']['allow'].append(tool)
    value['agents']['list'][0]['tools'] = {'deny': [tool]}
    plugin = {} if socket_path is None else {'projectBuildSocket': socket_path}
    value['plugins'] = {'entries': {'pixel-ods': {'config': plugin}}}
    path = tmp_path / 'openclaw.json'
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    result = invoke(path, tmp_path)
    assert result.returncode == 0, result.stderr
    updated = json.loads(Path(result.stdout.strip()).read_text())
    enabled = socket_path is not None and socket_path.startswith('/')
    assert (tool in updated['tools']['alsoAllow']) is enabled
    assert (tool in updated['tools']['sandbox']['tools']['allow']) is enabled
    assert (tool not in updated['agents']['list'][0]['tools']['deny']) is enabled


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
    assert {'pixel_ops_run', 'pixel_ods_workspace_preview', 'pixel_ods_workspace_bundle','pixel_ods_workspace_artifact', 'create_goal'}.issubset(value['tools']['alsoAllow'])
    assert 'pixel_ods_extension_proposal' in value['tools']['alsoAllow']
    assert 'pixel_ods_image_read' in value['tools']['alsoAllow']
    assert 'pixel_ods_image_read' in value['tools']['sandbox']['tools']['allow']
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


def test_document_delivery_overlay_preserves_an_explicit_owner_deny(tmp_path):
    tmp_path.chmod(0o700)
    value = configuration()
    tool = 'pixel_ods_workspace_artifact'
    value['agents']['list'][0]['tools'] = {'deny': [tool]}
    path = tmp_path / 'openclaw.json'
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    result = invoke(path, tmp_path)
    assert result.returncode == 0, result.stderr
    updated = json.loads(Path(result.stdout.strip()).read_text())
    assert tool in updated['tools']['alsoAllow']
    assert tool in updated['agents']['list'][0]['tools']['deny']


R11 = 'DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M.gguf'


def routed(model, reasoning):
    value = configuration(65536)
    value['models']['providers']['ods-local']['models'][0].update(
        id=model, name=f'ODS Local {model}', reasoning=reasoning)
    value['agents']['list'][0]['model'] = f'ods-local/{model}'
    return value


def render_thinking(folder, value, control, home):
    """Return the overlay's bytes for an answers file naming the config's own route.

    The input is written in the renderer's own format, so an "unchanged" result
    yields the same bytes a staged write would.
    """
    folder.mkdir(mode=0o700)
    path = folder / 'openclaw.json'
    path.write_bytes((json.dumps(value, indent=2, sort_keys=True) + '\n').encode())
    path.chmod(0o600)
    row = value['models']['providers']['ods-local']['models'][0]
    answers = {'modelProvider': 'ods-local', 'modelId': row['id'], 'modelName': row['name'],
               'modelImageInput': 'unknown'}
    if control is not None:
        answers['modelThinkingControl'] = control
    answers_path = folder / 'onboarding.json'
    answers_path.write_text(json.dumps(answers))
    answers_path.chmod(0o600)
    result = subprocess.run([sys.executable, str(WRITER), str(path), '3099', str(answers_path), str(home)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return (path if result.stdout.strip() == 'unchanged' else Path(result.stdout.strip())).read_bytes()


QWEN_COMPAT = {'thinkingFormat': 'qwen-chat-template'}


# T-U-4 Pixel config builder: a measured control replaces the "qwen" name rule.
@pytest.mark.parametrize('model,reasoning,control,expected', [
    # No control: today's name rule.
    ('Qwen3.5-9B', True, None, (True, QWEN_COMPAT, 'low', {'enable_thinking': True})),
    ('Qwen3.5-9B', False, None, (False, None, None, {'enable_thinking': False})),
    ('gpt-oss-20b', True, None, (True, None, None, None)),
    # enable_thinking: today's Qwen output, whatever the name says.
    ('gpt-oss-20b', True, 'enable_thinking', (True, QWEN_COMPAT, 'low', {'enable_thinking': True})),
    ('gpt-oss-20b', False, 'enable_thinking', (False, None, None, {'enable_thinking': False})),
    # always: no kwargs, reasoning true even when the flag says otherwise.
    (R11, True, 'always', (True, None, None, None)),
    (R11, False, 'always', (True, None, None, None)),
    # none: no kwargs, reasoning false.
    ('Qwen3.5-9B', True, 'none', (False, None, None, None)),
    ('Qwen3.5-9B', False, 'none', (False, None, None, None)),
])
def test_thinking_control_selects_the_openclaw_thinking_config(tmp_path, model, reasoning, control, expected):
    tmp_path.chmod(0o700)
    value = json.loads(render_thinking(tmp_path / 'render', routed(model, reasoning), control, tmp_path / '.openclaw'))
    row = value['models']['providers']['ods-local']['models'][0]
    agent = value['agents']['list'][0]
    assert (row['reasoning'], row.get('compat'), agent.get('thinkingDefault'),
            agent.get('params', {}).get('chat_template_kwargs')) == expected
    # The native model coordinator reads every result with an unchanged contract shape.
    contract = pixel_model_contract.projection(value)['contract']
    assert set(contract) == {'model', 'contextLength', 'maxTokens', 'reasoning', 'imageInput'}
    assert contract['reasoning'] is expected[0]


@pytest.mark.parametrize('model,reasoning,control', [
    ('Qwen3.5-9B', True, 'enable_thinking'), ('Qwen3.5-9B', False, 'enable_thinking'),
    ('gpt-oss-20b', True, 'always'), ('gpt-oss-20b', False, 'none')])
def test_control_that_agrees_with_the_name_rule_writes_todays_bytes(tmp_path, model, reasoning, control):
    tmp_path.chmod(0o700)
    home = tmp_path / '.openclaw'
    assert (render_thinking(tmp_path / 'measured', routed(model, reasoning), control, home)
            == render_thinking(tmp_path / 'named', routed(model, reasoning), None, home))


@pytest.mark.parametrize('control,reasoning', [('enable_thinking', True), ('always', True), ('none', False)])
def test_measured_control_leaves_nothing_a_previous_release_cannot_regenerate(tmp_path, control, reasoning):
    # The previous release's plugin schema rejects unknown keys and its renderer
    # never removes them, so the control must not add a plugin setting. After a
    # rollback its onboarding writer drops the control and its renderer (today's
    # rule) must regenerate exactly its own output from what this one wrote.
    tmp_path.chmod(0o700)
    home = tmp_path / '.openclaw'
    measured = render_thinking(tmp_path / 'measured', routed(R11, reasoning), control, home)
    named = render_thinking(tmp_path / 'named', routed(R11, reasoning), None, home)
    assert json.loads(measured)['plugins'] == json.loads(named)['plugins']
    assert render_thinking(tmp_path / 'rollback', json.loads(measured), None, home) == named
    # And the reverse: a measured control fully replaces the name-rule fields.
    assert render_thinking(tmp_path / 'upgrade', json.loads(named), control, home) == measured


@pytest.mark.parametrize('control', ['sometimes', 'ALWAYS', '', True, None])
def test_invalid_or_foreign_thinking_control_is_refused_without_staging(tmp_path, control):
    tmp_path.chmod(0o700)
    path = tmp_path / 'openclaw.json'
    path.write_text(json.dumps(routed(R11, True)))
    path.chmod(0o600)
    before = path.read_bytes()
    answers = {'modelProvider': 'ods-local', 'modelId': R11, 'modelName': f'ODS Local {R11}',
               'modelThinkingControl': control}
    if control is None:
        # A valid control measured for another model is never inherited.
        answers.update(modelId='Qwen3.5-9B', modelName='ODS Local Qwen3.5-9B', modelThinkingControl='always')
    answers_path = tmp_path / 'onboarding.json'
    answers_path.write_text(json.dumps(answers))
    answers_path.chmod(0o600)
    result = subprocess.run([sys.executable, str(WRITER), str(path), '3099', str(answers_path),
                             str(tmp_path / '.openclaw')], capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert ('route does not match' if control is None else 'invalid ODS Pixel thinking-control contract') in result.stderr
    assert path.read_bytes() == before
    assert not list(tmp_path.glob('.ods-pixel-runtime-budget.*'))
