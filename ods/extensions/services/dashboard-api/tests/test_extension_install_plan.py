import asyncio
import json
from unittest.mock import AsyncMock

import pytest
import yaml

from extension_install_plan import InstallPlanError, build_install_plan
from routers import extensions


def plan(graph, statuses=None, configured=lambda key: False):
    statuses = statuses or {}
    entries = [{'id': key, 'status': statuses.get(key, 'not_installed'), 'installable': True}
               for key in graph]
    return build_install_plan('app', entries, graph.__getitem__, configured)


def service(key, deps=(), fields=()):
    return {'id': key, 'depends_on': list(deps), 'env_vars': list(fields)}


def test_shared_dependencies_are_ordered_once_before_dependents():
    graph = {'app': service('app', ['left', 'right']), 'left': service('left', ['db']),
             'right': service('right', ['db']), 'db': service('db')}
    result = plan(graph, {'db': 'enabled', 'right': 'disabled'})
    assert [(step['extensionId'], step['action']) for step in result['steps']] == [
        ('db', 'none'), ('left', 'install'), ('right', 'enable'), ('app', 'install')]
    assert result['executionStarted'] is False


@pytest.mark.parametrize('state,action', [('enabled', 'none'), ('cli_installed', 'none'),
    ('disabled', 'enable'), ('stopped', 'enable'), ('installing', 'wait'),
    ('setting_up', 'wait'), ('error', 'blocked'), ('incompatible', 'blocked'), ('unhealthy', 'blocked')])
def test_actions_do_not_reinstall_or_replay_active_work(state, action):
    result = plan({'app': service('app')}, {'app': state})
    assert result['steps'][0]['action'] == action
    assert result['pending'] is (action == 'wait')
    assert result['blocked'] is (action == 'blocked')


def test_configuration_presence_does_not_return_credentials_or_defaults():
    graph = {'app': service('app', fields=[{'key': 'APP_SECRET', 'secret': True, 'required': True,
                                          'default': 'never-project-this'}])}
    result = plan(graph)
    assert result['requiresConfiguration'] is True
    assert result['steps'][0]['missingConfiguration'] == ['APP_SECRET']
    assert 'never-project-this' not in json.dumps(result)
    assert plan(graph, configured=lambda key: True)['requiresConfiguration'] is False
    assert plan(graph, {'app': 'enabled'})['requiresConfiguration'] is False


@pytest.mark.parametrize('graph', [
    {'app': service('app', ['missing'])},
    {'app': service('app', ['db']), 'db': service('db', ['app'])},
    {'app': service('different')},
    {'app': service('app', fields=[{'key': 'TOKEN', 'required': 'false'}])},
    {'app': service('app', fields=[{'key': 'TOKEN'}, {'key': 'TOKEN'}])},
])
def test_incomplete_ambiguous_or_cyclic_definitions_fail_closed(graph):
    with pytest.raises(InstallPlanError):
        plan(graph)


def test_protected_missing_dependency_is_not_implicitly_authorized():
    graph = {'app': service('app', ['core']), 'core': service('core')}
    entries = [{'id': key, 'status': 'not_installed', 'installable': True} for key in graph]
    result = build_install_plan('app', entries, graph.__getitem__, lambda key: False, {'core'})
    assert result['blocked'] is True
    assert result['steps'][0]['action'] == 'blocked'


def test_plan_endpoint_uses_installed_manifest_and_only_configuration_presence(tmp_path, monkeypatch):
    roots = [tmp_path / name for name in ['user', 'builtin', 'library']]
    for key, root in zip(['USER_EXTENSIONS_DIR', 'EXTENSIONS_DIR', 'EXTENSIONS_LIBRARY_DIR'], roots):
        root.mkdir()
        monkeypatch.setattr(extensions, key, root)
    directory = roots[0] / 'app'
    directory.mkdir()
    (directory / 'manifest.yaml').write_text(yaml.safe_dump({'service': service('app', fields=[
        {'key': 'APP_SECRET', 'secret': True, 'required': True}])}))
    monkeypatch.setattr(extensions, 'extensions_catalog', AsyncMock(return_value={'extensions': [
        {'id': 'app', 'status': 'disabled', 'installable': True}]}))
    monkeypatch.setattr('config._read_env_value', lambda key: 'private-value')
    result = asyncio.run(extensions.extension_install_plan('app', api_key='test'))
    assert result['steps'][0]['action'] == 'enable'
    assert result['requiresConfiguration'] is False
    assert 'private-value' not in json.dumps(result)
    (directory / 'manifest.yaml').unlink()
    with pytest.raises(extensions.HTTPException) as error:
        asyncio.run(extensions.extension_install_plan('app', api_key='test'))
    assert error.value.status_code == 400
