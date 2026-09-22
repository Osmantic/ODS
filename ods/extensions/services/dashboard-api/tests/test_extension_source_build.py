import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from extension_source_build import inspect_source_builds, verify_source_receipts
from extension_recipe_validation import validate_recipe
from extension_recipe_package import publish_package
from extension_recipe_drafts import save_draft
from test_extension_recipe_validation import candidate, SCHEMA, scan, ODS
from test_extension_recipe_package import upstream
from test_extension_recipe_drafts import evidence
from routers import extensions


def proposal():
    value = candidate()
    service = value['compose']['services']['apache-answer']
    service.update(build={'context': value['repository'] + '.git#' + value['commit'] + ':app'},
                   image='ods-source-apache-answer:' + value['commit'], pull_policy='never')
    return value


@pytest.mark.parametrize('build', [
    '.', {'context': '.'}, {'context': 'https://github.com/other/repo.git#' + 'a' * 40},
    {'context': 'https://github.com/apache/answer.git#main'},
    {'context': 'https://github.com/apache/answer.git#' + 'a' * 40 + ':../secret'},
    {'context': 'https://github.com/apache/answer.git#' + 'a' * 40, 'dockerfile': '../Dockerfile'},
    {'context': 'https://github.com/apache/answer.git#' + 'a' * 40, 'network': 'host'},
    {'context': 'https://github.com/apache/answer.git#' + 'a' * 40, 'ssh': ['default']},
    {'context': 'https://github.com/apache/answer.git#' + 'a' * 40, 'dockerfile_inline': 'FROM scratch', 'dockerfile': 'Dockerfile'},
])
def test_rejects_unbound_or_host_dependent_builds(build):
    value = proposal()
    value['compose']['services']['apache-answer']['build'] = build
    assert validate_recipe(value, SCHEMA, set(), scan)['valid'] is False


def test_source_recipe_keeps_untrusted_container_checks():
    value = proposal()
    assert validate_recipe(value, SCHEMA, set(), scan)['valid'] is True
    value['compose']['services']['apache-answer']['privileged'] = True
    assert validate_recipe(value, SCHEMA, set(), scan)['valid'] is False


@pytest.mark.parametrize('change', [{'image': 'somebody/remote:latest'}, {'pull_policy': 'always'},
                                    {'build': {'context': 'https://github.com/apache/answer.git#' + 'a' * 40,
                                               'dockerfile': ''}}])
def test_source_recipe_cannot_redirect_built_image_or_omit_dockerfile(change):
    value = proposal()
    value['compose']['services']['apache-answer'].update(change)
    assert not validate_recipe(value, SCHEMA, set(), scan)['valid']


def test_source_evidence_required_for_publication(tmp_path):
    value = proposal()
    with pytest.raises(ValueError, match='evidence'):
        publish_package(tmp_path, value, evidence(value), upstream(value))
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('inline', [False, True])
def test_preparation_fetches_pinned_dockerfile_and_staged_enable_rechecks_it(monkeypatch, tmp_path, inline):
    value = proposal()
    if inline:
        value['compose']['services']['apache-answer']['build']['dockerfile_inline'] = 'FROM scratch\nCOPY . /app\n'
    library = tmp_path / 'library'; library.mkdir()
    drafts = tmp_path / '.extension-recipe-drafts'; drafts.mkdir()
    draft = save_draft(drafts, 'owner', value, evidence(value))
    monkeypatch.setattr(extensions, '_extensions_lock_path', lambda: tmp_path / '.lock')
    monkeypatch.setattr(extensions, 'EXTENSIONS_LIBRARY_DIR', library)
    monkeypatch.setattr(extensions, 'USER_EXTENSIONS_DIR', tmp_path / 'user')
    monkeypatch.setattr(extensions, 'EXTENSIONS_DIR', ODS / 'extensions/services')
    monkeypatch.setattr(extensions, 'extensions_catalog', AsyncMock(return_value={'extensions': []}))
    monkeypatch.setattr('extension_github.inspect_repository', AsyncMock(return_value=upstream(value)))
    file = AsyncMock(return_value={'repository': value['repository'], 'commit': value['commit'],
        'path': 'app/Dockerfile', 'blob': 'b' * 40, 'content': 'FROM scratch',
        'evidenceScope': 'repository-file-at-commit'})
    monkeypatch.setattr('extension_github.inspect_file', file)
    response = asyncio.run(extensions._prepare_github_draft(draft['draftId'], 'owner'))
    assert json.loads(response.body)['state'] == 'available'
    if inline:
        file.assert_not_awaited()
        metadata = json.loads((library / 'apache-answer/upstream.json').read_text(encoding='utf-8'))
        assert metadata['sourceFiles'][0]['kind'] == 'proposed-dockerfile'
        assert 'blob' not in metadata['sourceFiles'][0]
    else:
        file.assert_awaited_once_with(value['repository'], value['commit'], 'app/Dockerfile')
    with extensions._staged_library_extension('apache-answer', tmp_path / 'user/apache-answer') as (staged, _):
        extensions._scan_compose_content(staged / 'compose.yaml')
        original = (staged / 'compose.yaml').read_text(encoding='utf-8')
        assert value['compose']['services']['apache-answer']['build']['context'] in original
        disabled = staged / 'compose.yaml.disabled'
        (staged / 'compose.yaml').rename(disabled)
        extensions._scan_compose_content(disabled)
        disabled.write_text(original.replace(value['commit'], 'c' * 40), encoding='utf-8')
        with pytest.raises(extensions.HTTPException):
            extensions._scan_compose_content(disabled)


def test_mismatched_source_file_cannot_supply_evidence():
    value = proposal()
    file = AsyncMock(return_value={'repository': value['repository'], 'commit': 'c' * 40,
        'path': 'app/Dockerfile', 'blob': 'b' * 40, 'content': 'FROM scratch',
        'evidenceScope': 'repository-file-at-commit'})
    with pytest.raises(ValueError):
        asyncio.run(inspect_source_builds(value, file))


@pytest.mark.parametrize('text', ['', ' ', 'FROM scratch\x00', 'x' * 24577,
                                 'FROM alpine\nRUN echo $HOME', 'FROM alpine\nRUN echo ${APACHE_ANSWER_TOKEN}'])
def test_inline_dockerfile_rejects_empty_oversize_or_host_interpolation(text):
    value = proposal()
    value['compose']['services']['apache-answer']['build']['dockerfile_inline'] = text
    assert not validate_recipe(value, SCHEMA, set(), scan)['valid']


def test_inline_receipt_binds_exact_bytes_without_claiming_upstream_evidence():
    value = proposal()
    build = value['compose']['services']['apache-answer']['build']
    build['dockerfile_inline'] = 'FROM alpine\nRUN echo $$HOME\n'
    assert validate_recipe(value, SCHEMA, set(), scan)['valid']
    reader = AsyncMock()
    receipts = asyncio.run(inspect_source_builds(value, reader))
    reader.assert_not_called()
    verify_source_receipts(value, receipts)
    build['dockerfile_inline'] += '# changed\n'
    with pytest.raises(ValueError, match='changed'):
        verify_source_receipts(value, receipts)
