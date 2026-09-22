import asyncio
import copy
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml
from starlette.requests import Request

from extension_recipe_validation import validate_recipe
from routers import extensions

ODS = Path(__file__).resolve().parents[4]
SCHEMA = json.loads((ODS / 'extensions/schema/service-manifest.v1.json').read_text(encoding='utf-8'))


def candidate():
    directory = ODS / 'extensions/library/services/apache-answer'
    return {'repository': 'https://github.com/apache/answer', 'commit': 'a' * 40,
            'manifest': yaml.safe_load((directory / 'manifest.yaml').read_text(encoding='utf-8')),
            'compose': yaml.safe_load((directory / 'compose.yaml').read_text(encoding='utf-8'))}


def scan(path):
    try:
        extensions._scan_compose_content(path)
        return True
    except extensions.HTTPException:
        return False


def test_real_recipe_passes_without_installation_registration_or_provenance_claim():
    paths = []
    def scanner(path):
        paths.append(path)
        return scan(path)
    result = validate_recipe(candidate(), SCHEMA, set(), scanner)
    assert result['valid'] and result['errors'] == []
    assert not any(result[key] for key in ('installationStarted', 'registered', 'runtimeVerified', 'provenanceVerified'))
    assert paths and all(not path.exists() for path in paths)


@pytest.mark.parametrize('command,restart,expected', [
    (['answer', '--version'], 'no', None),
    (None, 'no', 'verification-command-required'),
    ('answer --version', 'no', 'verification-command-required'),
    (['answer', '--version'], 'unless-stopped', 'one-shot-restart-forbidden'),
])
def test_portless_cli_requires_exit_verification_instead_of_a_server_probe(command, restart, expected):
    value = candidate()
    value['manifest']['service'].update(port=0, health='', startup_check=False)
    primary = value['compose']['services']['apache-answer']
    primary.pop('healthcheck', None)
    primary.update(command=command, restart=restart)
    result = validate_recipe(value, SCHEMA, set(), scan)
    if expected is None:
        assert result['valid'], result['errors']
    else:
        assert expected in {error['code'] for error in result['errors']}


@pytest.mark.parametrize('change,code', [
    ({'image': 'apache/answer:latest'}, 'image-digest-required'),
    ({'privileged': True}, 'compose-policy-rejected'),
    ({'volumes': ['/var/run/docker.sock:/var/run/docker.sock']}, 'compose-policy-rejected'),
    ({'network_mode': 'host'}, 'compose-policy-rejected'),
    ({'container_name': 'ods-dashboard'}, 'container-name-conflict'),
    ({'build': '.'}, 'source-build-review-required'),
    ({'healthcheck': {'test': ['NONE']}}, 'healthcheck-required'),
    ({'env_file': '/ods/.env'}, 'external-service-directives-review-required'),
    ({'extends': {'file': '/other/compose.yaml'}}, 'external-service-directives-review-required'),
    ({'use_api_socket': True}, 'external-service-directives-review-required'),
])
def test_proposals_cannot_gain_curated_library_privileges(change, code):
    value = candidate()
    value['compose']['services']['apache-answer'].update(change)
    result = validate_recipe(value, SCHEMA, set(), scan)
    assert result['valid'] is False
    assert code in {error['code'] for error in result['errors']}


def test_reserved_extension_and_companion_names_cannot_replace_existing_services():
    value = candidate()
    result = validate_recipe(value, SCHEMA, {'apache-answer'}, scan)
    assert 'extension-already-exists' in {e['code'] for e in result['errors']}
    value['compose']['services']['dashboard'] = copy.deepcopy(value['compose']['services']['apache-answer'])
    result = validate_recipe(value, SCHEMA, {'dashboard'}, scan)
    assert 'service-name-conflict' in {e['code'] for e in result['errors']}


@pytest.mark.parametrize('expression', ['${DASHBOARD_API_KEY}', '$DASHBOARD_API_KEY',
    '${APACHE_ANSWER_OPTION:-${DASHBOARD_API_KEY}}', '$$${DASHBOARD_API_KEY}'])
def test_recipe_cannot_read_other_services_host_credentials(expression):
    value = candidate()
    value['compose']['services']['apache-answer']['environment']['TOKEN'] = expression
    result = validate_recipe(value, SCHEMA, set(), scan)
    assert 'undeclared-host-variable' in {e['code'] for e in result['errors']}
    assert 'DASHBOARD_API_KEY' not in json.dumps(result)


def test_declaring_global_key_does_not_grant_ownership():
    value = candidate()
    value['manifest']['service']['env_vars'] = [{'key': 'DASHBOARD_API_KEY', 'required': True}]
    value['compose']['services']['apache-answer']['environment']['TOKEN'] = '${DASHBOARD_API_KEY}'
    result = validate_recipe(value, SCHEMA, set(), scan)
    assert {'configuration-ownership-required', 'undeclared-host-variable'} <= {e['code'] for e in result['errors']}


def test_own_declared_variables_and_escaped_container_variables_are_allowed():
    value = candidate()
    value['manifest']['service']['env_vars'] = [{'key': 'APACHE_ANSWER_TOKEN', 'required': True}]
    value['compose']['services']['apache-answer']['environment'].update(
        TOKEN='${APACHE_ANSWER_TOKEN:?Set the token}', LITERAL='$${CONTAINER_VARIABLE}')
    result = validate_recipe(value, SCHEMA, set(), scan)
    assert result['valid'], result['errors']


def test_proposed_hook_cannot_resolve_to_existing_host_script():
    value = candidate()
    value['manifest']['service']['setup_hook'] = 'setup.sh'
    result = validate_recipe(value, SCHEMA, set(), scan)
    assert 'hook-files-review-required' in {e['code'] for e in result['errors']}


def test_schema_errors_do_not_echo_values_and_digest_binds_the_entire_proposal():
    value = candidate()
    first = validate_recipe(value, SCHEMA, set(), scan)
    value['commit'] = 'b' * 40
    assert validate_recipe(value, SCHEMA, set(), scan)['recipeDigest'] != first['recipeDigest']
    value['manifest']['service']['port'] = 'private-value-must-not-be-returned'
    result = validate_recipe(value, SCHEMA, set(), scan)
    assert not result['valid']
    assert 'private-value' not in json.dumps(result)


def test_other_applications_named_volumes_cannot_be_claimed():
    value = candidate()
    value['compose']['volumes']['apache-answer-data'] = {'external': True, 'name': 'other-data'}
    result = validate_recipe(value, SCHEMA, set(), scan)
    assert 'external-resource-review-required' in {e['code'] for e in result['errors']}


def test_compose_includes_cannot_read_uninspected_host_definitions():
    value = candidate()
    value['compose']['include'] = ['/ods/private/compose.yaml']
    result = validate_recipe(value, SCHEMA, set(), scan)
    assert 'compose-external-directives-review-required' in {e['code'] for e in result['errors']}


def request(payload):
    async def receive():
        return {'type': 'http.request', 'body': json.dumps(payload).encode(), 'more_body': False}
    return Request({'type': 'http', 'method': 'POST', 'headers': []}, receive)


def test_endpoint_uses_actual_schema_and_catalog_without_persisting_recipe(monkeypatch, tmp_path):
    monkeypatch.setattr(extensions, 'EXTENSIONS_DIR', ODS / 'extensions/services')
    monkeypatch.setattr(extensions, 'USER_EXTENSIONS_DIR', tmp_path / 'user')
    monkeypatch.setattr(extensions, 'EXTENSIONS_LIBRARY_DIR', tmp_path / 'library')
    monkeypatch.setattr(extensions, 'extensions_catalog', AsyncMock(return_value={'extensions': []}))
    response = asyncio.run(extensions.extension_github_validate_recipe(request(candidate()), api_key='test'))
    assert response.headers['cache-control'] == 'no-store'
    assert json.loads(response.body)['valid'] is True


def test_different_id_cannot_disguise_an_existing_repository(monkeypatch, tmp_path):
    monkeypatch.setattr(extensions, 'EXTENSIONS_DIR', ODS / 'extensions/services')
    monkeypatch.setattr(extensions, 'EXTENSIONS_LIBRARY_DIR', tmp_path / 'library')
    monkeypatch.setattr(extensions, 'USER_EXTENSIONS_DIR', tmp_path / 'user')
    monkeypatch.setattr(extensions, 'extensions_catalog', AsyncMock(return_value={'extensions': []}))
    directory = tmp_path / 'user/renamed-answer'
    directory.mkdir(parents=True)
    (directory / 'upstream.json').write_text(json.dumps({'repository': 'https://github.com/Apache/Answer.git'}))
    response = asyncio.run(extensions.extension_github_validate_recipe(request(candidate()), api_key='test'))
    result = json.loads(response.body)
    assert result['valid'] is False
    assert result['existingExtensionIds'] == ['renamed-answer']
    assert result['errors'][0]['code'] == 'repository-already-exists'


def test_uncatalogued_local_directory_name_is_reserved(monkeypatch, tmp_path):
    monkeypatch.setattr(extensions, 'EXTENSIONS_DIR', ODS / 'extensions/services')
    monkeypatch.setattr(extensions, 'EXTENSIONS_LIBRARY_DIR', tmp_path / 'library')
    monkeypatch.setattr(extensions, 'USER_EXTENSIONS_DIR', tmp_path / 'user')
    monkeypatch.setattr(extensions, 'extensions_catalog', AsyncMock(return_value={'extensions': []}))
    (tmp_path / 'user/apache-answer').mkdir(parents=True)
    response = asyncio.run(extensions.extension_github_validate_recipe(request(candidate()), api_key='test'))
    result = json.loads(response.body)
    assert result['valid'] is False
    assert 'extension-already-exists' in {e['code'] for e in result['errors']}


@pytest.mark.parametrize('payload', [{}, {'repository': 'https://localhost/private'},
    {**candidate(), 'commit': 'main'}, {**candidate(), 'extra': 'never-used'}])
def test_invalid_proposal_is_rejected(payload):
    with pytest.raises(ValueError):
        validate_recipe(payload, SCHEMA, set(), scan)
