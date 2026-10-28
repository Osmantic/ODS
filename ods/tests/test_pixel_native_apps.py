"""Reviewed native app grants and their installer/owner transaction contract."""
import ast
import copy
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))
from pixel_connections import native_apps as apps, planner, state  # noqa: E402

OVERLAY = ROOT / 'installers/lib/pixel-runtime-budget.py'


def config():
    return {'plugins': {'allow': ['pixel-ods', 'llama-cpp'], 'entries': {}, 'load': {'paths': []}},
            'tools': {'alsoAllow': ['exec', 'other__read'],
                      'sandbox': {'tools': {'allow': ['exec', 'other__read']}}},
            'agents': {'defaults': {}, 'list': [{'id': 'pixel', 'tools': {'deny': ['message']}}]},
            'mcp': {'servers': {'other': {'transport': 'stdio', 'command': '/qa/other'}}}}


def record(chosen):
    return {'schemaVersion': 1, 'kind': 'ods-portal-connections', 'updatedAt': '2026-10-28T12:00:00Z',
            'ownerTimeZone': None, 'connectors': {}, 'nativeApps': chosen}


def target(chosen):
    return dict(planner.limbs(None), userTimezone=None, plugins=[], nativeApps=chosen)


def test_catalog_discloses_provider_reach_and_model_sharing_without_edit_tools():
    public = apps.public_catalog()
    assert set(public[0]) == {'id', 'label', 'summary', 'tools', 'details'}
    assert public[0]['tools'] == ['notion-search', 'notion-fetch']
    assert 'other sources' in ' '.join(public[0]['details'])
    assert 'selected model provider' in ' '.join(public[0]['details'])
    public[0]['tools'].append('notion-delete')
    assert apps.public_catalog()[0]['tools'] == ['notion-search', 'notion-fetch']


def test_stage_enable_disconnect_only_change_the_reviewed_server_and_exact_grants():
    original = config()
    staged = planner.plan_config(original, target({'notion': False}), install_dir='/qa/pixel')
    enabled = planner.plan_config(staged, target({'notion': True}), install_dir='/qa/pixel')
    disconnected = planner.plan_config(enabled, target({'notion': False}), install_dir='/qa/pixel')
    names = apps.tool_names('notion')
    for value, on in ((staged, False), (enabled, True), (disconnected, False)):
        assert value['mcp']['servers']['other'] == original['mcp']['servers']['other']
        assert value['mcp']['servers']['ods-notion'] == apps.server('notion', on)
        for allowed in (value['tools']['alsoAllow'], value['tools']['sandbox']['tools']['allow']):
            assert set(names).issubset(allowed) is on
            assert 'other__read' in allowed
            assert 'bundle-mcp' not in allowed
        denied = value['agents']['list'][0]['tools']['deny']
        assert set(names).issubset(denied) is not on
        assert 'message' in denied
        assert apps.configured_choices(value) == {'notion': on}
        assert planner.plan_config(value, target({'notion': on}), install_dir='/qa/pixel') == value
    assert 'allow' not in staged['agents']['list'][0]['tools'], 'preparing consent must not change unrelated access'
    # Disconnect keeps the narrowed policy; dropping it would restore implicit
    # broad MCP access merely because the managed app was switched off.
    assert set(apps.NATIVE_MCP_BASE_ALLOW).issubset(disconnected['agents']['list'][0]['tools']['allow'])
    previous_policy = disconnected['agents']['list'][0]['tools'].pop('allow')
    assert staged == disconnected
    disconnected['agents']['list'][0]['tools']['allow'] = previous_policy
    assert original == config(), 'planning must not mutate the live input'


@pytest.mark.parametrize('collision', [None, {}, {'enabled': 1},
    dict(apps.server('notion', False), headers={'Authorization': 'private'}),
    dict(apps.server('notion', False), url='https://other.example/mcp'),
    dict(apps.server('notion', False), toolFilter={'include': ['notion-delete']})])
def test_reserved_server_collision_is_not_overwritten_or_granted(collision):
    original = config()
    original['mcp']['servers']['ods-notion'] = collision
    before = copy.deepcopy(original)
    with pytest.raises(planner.PlanError, match='registry collision'):
        planner.plan_config(original, target({'notion': True}), install_dir='/qa/pixel')
    assert original == before
    assert apps.configured_choices(original) == {}


@pytest.mark.parametrize('name', ['ods notion', 'ODS-NOTION', 'ods-notion ', 'ods-notion/' + 'x' * 30])
def test_sanitized_alias_cannot_borrow_a_managed_apps_exact_tool_grants(name):
    original = config()
    original['mcp']['servers'][name] = {'transport': 'stdio', 'command': '/qa/other'}
    before = copy.deepcopy(original)
    with pytest.raises(planner.PlanError):
        planner.plan_config(original, target({'notion': True}), install_dir='/qa/pixel')
    assert original == before
    disabled = planner.plan_config(original, target({'notion': False}), install_dir='/qa/pixel')
    assert disabled['mcp']['servers'][name] == original['mcp']['servers'][name]
    assert set(apps.tool_names('notion')).issubset(disabled['agents']['list'][0]['tools']['deny'])


def test_existing_agent_additive_policy_is_refused_without_silently_migrating_it():
    original = config()
    original['agents']['list'][0]['tools']['alsoAllow'] = ['owner__special']
    before = copy.deepcopy(original)
    with pytest.raises(planner.PlanError):
        planner.plan_config(original, target({'notion': True}), install_dir='/qa/pixel')
    assert original == before
    disabled = planner.plan_config(original, target({'notion': False}), install_dir='/qa/pixel')
    assert disabled['agents']['list'][0]['tools']['alsoAllow'] == ['owner__special']
    assert 'allow' not in disabled['agents']['list'][0]['tools']
    assert set(apps.tool_names('notion')).issubset(disabled['agents']['list'][0]['tools']['deny'])


def test_legacy_target_preserves_native_registry_and_existing_effective_policy():
    value = planner.plan_config(config(), target({'notion': True}), install_dir='/qa/pixel')
    legacy = dict(planner.limbs(None), userTimezone=None, plugins=[])
    updated = planner.plan_config(value, legacy, install_dir='/qa/pixel')
    assert updated == value
    assert 'nativeApps' not in planner.target(legacy)


def test_explicit_owner_policies_are_preserved_and_later_plugin_grants_stay_additive():
    value = config()
    value['tools']['allow'] = ['group:fs', 'other__read']
    value['tools']['deny'] = ['other__private']
    value['agents']['list'][0]['tools']['allow'] = ['other__read']
    updated = planner.plan_config(value, target({'notion': True}), install_dir='/qa/pixel')
    assert updated['tools']['allow'] == value['tools']['allow']
    assert updated['tools']['deny'] == value['tools']['deny']
    assert updated['agents']['list'][0]['tools']['allow'] == ['other__read']
    # Reviewed ODS plugin families remain eligible; the native profile and deny
    # layers still decide which of their tools are active.
    enabled = planner.plan_config(config(), target({'notion': True}), install_dir='/qa/pixel')
    default = planner.plan_config(enabled, target({'notion': False}), install_dir='/qa/pixel')
    allowed = default['agents']['list'][0]['tools']['allow']
    assert set(apps.NATIVE_MCP_BASE_ALLOW).issubset(allowed)
    assert 'allow' not in default['tools']
    assert 'bundle-mcp' not in allowed and 'group:plugins' not in allowed
    more = planner.plan_config(default, dict(target({'notion': False}), plugins=['memory-wiki']), install_dir='/qa/pixel')
    assert 'wiki_get' in more['tools']['alsoAllow']
    assert more['agents']['list'][0]['tools']['allow'] == allowed


@pytest.mark.parametrize('bad', [[], {'unknown': True}, {'notion': 1}, {'notion': 'yes'}, None])
def test_browser_choices_cannot_select_arbitrary_servers_or_nonboolean_grants(bad):
    with pytest.raises(planner.PlanError, match='native app target'):
        planner.target(target(bad))


def test_root_record_restores_disabled_and_enabled_choices_without_google():
    for enabled in (False, True):
        saved = record({'notion': enabled, 'future-app': True})
        assert state.validate(saved) is saved
        assert state.native_app_choices(saved) == {'notion': enabled}
        expected = apps.answers({'notion': enabled})
        assert state.onboarding_overrides(saved, google_custody=False) == {'nativeMcpApps': expected}
        answers = state.connection_answers(saved, google_custody=False)
        assert state.valid_connection_answers(answers)
        assert answers['nativeMcpApps'] == expected
    assert state.onboarding_overrides(record({'future-app': True}), google_custody=False) == {}
    assert state.valid_connection_answers({key: value for key, value in answers.items() if key != 'nativeMcpApps'})


@pytest.mark.parametrize('bad', [{'notion': 1}, {'notion': 'yes'}, {'Notion App': True}, [],
    {f'app-{number}': True for number in range(33)}])
def test_root_record_choices_have_no_endpoints_credentials_or_nonboolean_state(bad):
    with pytest.raises(state.StateError, match='native app choice'):
        state.validate(record(bad))


@pytest.mark.parametrize('field,value', [('id', 'unreviewed'), ('serverName', 'other'),
    ('server', dict(apps.server('notion', True), headers={'Authorization': 'private'})),
    ('server', dict(apps.server('notion', True), enabled=1)), ('tools', ['exec'])])
def test_owner_answer_pipe_refuses_modified_reviewed_contract(field, value):
    answers = state.connection_answers(record({'notion': True}), google_custody=False)
    answers['nativeMcpApps'][0][field] = value
    assert not state.valid_connection_answers(answers)


def test_standalone_overlay_uses_identical_validator_contract_and_transform():
    source = OVERLAY.read_text(encoding='utf-8')
    tree = ast.parse(source)
    for name in ('valid_native_app_answers', 'load_native_apps'):
        node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == name)
        assert ast.get_source_segment(source, node) == inspect.getsource(getattr(apps, name)).rstrip()
    for name in ('NATIVE_MCP_CONTRACTS', 'NATIVE_MCP_BASE_ALLOW'):
        node = next(item for item in tree.body if isinstance(item, ast.Assign)
                    and any(isinstance(key, ast.Name) and key.id == name for key in item.targets))
        assert ast.literal_eval(node.value) == getattr(apps, name)


@pytest.mark.skipif(os.name != 'posix', reason='POSIX installed Root program import')
def test_installed_connection_package_is_self_contained_for_onboarding_and_planning(tmp_path):
    source = (ROOT / 'installers/lib/pixel-host-install.sh').read_text(encoding='utf-8')
    block = source.split('_ods_pixel_install_access_service() {', 1)[1].split("<<'PY'\n", 1)[1].split('\nPY\n', 1)[0]
    tree = ast.parse(block)
    loop = next(node for node in tree.body if isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple)
                and any(isinstance(item, ast.Constant) and item.value == 'native_mcp.py' for item in node.iter.elts))
    package = tmp_path / 'pixel_connections'
    package.mkdir()

    def write(path, data, mode):
        path.write_bytes(data)
        path.chmod(mode)

    exec(compile(ast.Module(body=[loop], type_ignores=[]), '<installed connection package>', 'exec'),
         {'connections_package': package, 'code_source': ROOT, 'write': write})
    check = ('import sys; sys.path.insert(0, sys.argv[1]); '
             'from pixel_connections import native_apps, native_mcp, planner, state; '
             'assert state.valid_connection_answers(state.connection_answers(None, google_custody=False)); '
             'assert native_apps.answers({"notion": False})[0]["server"]["enabled"] is False')
    result = subprocess.run([sys.executable, '-I', '-c', check, str(tmp_path)],
                            cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('enabled', [False, True])
def test_installer_reapplies_root_choices_and_refuses_collision_without_replacing_live_file(tmp_path, enabled):
    from test_pixel_runtime_budget import configuration
    tmp_path.chmod(0o700)
    value = configuration()
    value['mcp'] = config()['mcp']
    path = tmp_path / 'openclaw.json'
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    answers = tmp_path / 'onboarding.json'
    restored = state.onboarding_overrides(record({'notion': enabled}), google_custody=False)
    restored.update(modelProvider='ods-local', modelId='Qwen3.5-9B', modelName='ODS Local Qwen3.5-9B')
    answers.write_text(json.dumps(restored))
    answers.chmod(0o600)
    command = [sys.executable, str(OVERLAY), str(path), '3099', str(answers), str(tmp_path / '.openclaw')]
    result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    updated = json.loads(Path(result.stdout.strip()).read_text())
    assert apps.configured_choices(updated) == {'notion': enabled}
    assert updated['mcp']['servers']['other'] == value['mcp']['servers']['other']
    value['mcp']['servers']['ods-notion'] = {'transport': 'stdio', 'command': '/qa/collision'}
    path.write_text(json.dumps(value))
    before = path.read_bytes()
    result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert result.returncode != 0 and 'registry collision' in result.stderr
    assert path.read_bytes() == before
