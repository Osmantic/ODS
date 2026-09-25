import copy
import asyncio
import json
from pathlib import Path

import pytest
import yaml

from extension_catalog_local import merge_local_catalog

ODS = Path(__file__).resolve().parents[4]
SCHEMA = ODS / 'extensions/schema/service-manifest.v1.json'


def install_definition(root, disabled=False):
    folder = root / 'apache-answer'
    folder.mkdir(parents=True)
    source = ODS / 'extensions/library/services/apache-answer'
    document = yaml.safe_load((source / 'manifest.yaml').read_text(encoding='utf-8'))
    document['service']['env_vars'] = [{'key': 'APACHE_ANSWER_PASSWORD', 'secret': True,
                                      'required': True, 'default': 'never-expose-this'}]
    (folder / 'manifest.yaml').write_text(yaml.safe_dump(document), encoding='utf-8')
    (folder / ('compose.yaml.disabled' if disabled else 'compose.yaml')).write_text('services: {}')
    return folder, document


@pytest.mark.parametrize('disabled', [False, True])
def test_local_definition_appears_without_implying_runtime_success(tmp_path, disabled):
    install_definition(tmp_path, disabled)
    entries = merge_local_catalog([], tmp_path, SCHEMA)
    assert [e['id'] for e in entries] == ['apache-answer']
    assert 'never-expose-this' not in json.dumps(entries)
    assert 'status' not in entries[0]
    assert entries[0]['env_vars'][0]['key'] == 'APACHE_ANSWER_PASSWORD'


def test_shipped_definition_is_not_duplicated_or_mutated(tmp_path):
    install_definition(tmp_path)
    shipped = [{'id': 'apache-answer', 'name': 'Shipped'}]
    before = copy.deepcopy(shipped)
    assert merge_local_catalog(shipped, tmp_path, SCHEMA) == before
    assert shipped == before


@pytest.mark.parametrize('failure', ['mismatch', 'invalid', 'incomplete', 'oversized', 'draft'])
def test_invalid_or_unpublished_definitions_do_not_enter_catalog(tmp_path, failure):
    folder, document = install_definition(tmp_path)
    if failure == 'mismatch':
        document['service']['id'] = 'another'
        (folder / 'manifest.yaml').write_text(yaml.safe_dump(document))
    elif failure == 'invalid':
        (folder / 'manifest.yaml').write_text('not: [valid')
    elif failure == 'oversized':
        (folder / 'manifest.yaml').write_text('x' * 262145)
    elif failure == 'draft':
        folder.rename(tmp_path / '.recipe-draft')
    else:
        (folder / 'compose.yaml').unlink()
    assert merge_local_catalog([], tmp_path, SCHEMA) == []


def test_router_uses_dynamic_catalog_for_detail_and_list(monkeypatch, tmp_path):
    from routers import extensions
    install_definition(tmp_path)
    monkeypatch.setattr(extensions, 'EXTENSION_CATALOG', [])
    monkeypatch.setattr(extensions, 'USER_EXTENSIONS_DIR', tmp_path)
    monkeypatch.setattr(extensions, 'EXTENSIONS_DIR', ODS / 'extensions/services')
    monkeypatch.setattr(extensions, 'SERVICES', {})
    monkeypatch.setattr(extensions, 'DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setattr(extensions, 'EXTENSIONS_LIBRARY_DIR', tmp_path / 'absent-library')
    monkeypatch.setattr(extensions, '_check_agent_health', lambda: False)
    monkeypatch.setattr(extensions, '_cleanup_stale_progress', lambda: None)
    monkeypatch.setattr(extensions, '_read_progress', lambda identifier: None)
    monkeypatch.setattr('helpers.get_cached_services', lambda: [])
    monkeypatch.setattr('user_extensions.get_user_services_cached', lambda directory: {})
    result = asyncio.run(extensions.extensions_catalog(api_key='test'))
    assert len(result['extensions']) == 1
    assert result['extensions'][0]['id'] == 'apache-answer'
    assert result['extensions'][0]['status'] == 'stopped'
    assert result['extensions'][0]['source'] == 'user'
    detail = asyncio.run(extensions.extension_detail('apache-answer', api_key='test'))
    assert detail['id'] == 'apache-answer'
    assert detail['status'] == 'stopped'
