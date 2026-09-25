import copy
import json
import asyncio
from unittest.mock import AsyncMock

import pytest

from extension_recipe_drafts import read_draft, save_draft
from test_extension_recipe_validation import candidate, SCHEMA, scan, request, ODS
from extension_recipe_validation import validate_recipe
from routers import extensions


def evidence(proposal):
    return {**validate_recipe(proposal, SCHEMA, set(), scan), 'existingExtensionIds': []}


def test_draft_is_recoverable_idempotent_and_separate_from_installation(tmp_path):
    proposal = candidate()
    first = save_draft(tmp_path, 'owner', proposal, evidence(proposal))
    assert first == save_draft(tmp_path, 'owner', proposal, evidence(proposal))
    assert read_draft(tmp_path, 'owner', first['draftId']) == proposal
    assert len(list(tmp_path.glob('*.json'))) == 1
    assert first['registered'] is False and first['installationStarted'] is False
    assert first['requiresRevalidation'] is True
    assert not list(tmp_path.glob('*/manifest.yaml'))


def test_owner_and_content_cannot_be_substituted(tmp_path):
    proposal = candidate()
    result = save_draft(tmp_path, 'owner', proposal, evidence(proposal))
    with pytest.raises(ValueError):
        read_draft(tmp_path, 'other-owner', result['draftId'])
    path = tmp_path / (result['draftId'] + '.json')
    document = json.loads(path.read_text())
    document['candidate']['commit'] = 'b' * 40
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        read_draft(tmp_path, 'owner', result['draftId'])
    with pytest.raises(ValueError):
        save_draft(tmp_path, 'owner', proposal, evidence(proposal))


def test_invalid_or_stale_validation_cannot_be_saved(tmp_path):
    proposal = candidate()
    validation = evidence(proposal)
    for key, value in [('valid', False), ('recipeDigest', '0' * 64), ('existingExtensionIds', ['existing'])]:
        with pytest.raises(ValueError):
            save_draft(tmp_path, 'owner', proposal, {**validation, key: value})
    changed = copy.deepcopy(proposal)
    changed['commit'] = 'b' * 40
    with pytest.raises(ValueError):
        save_draft(tmp_path, 'owner', changed, validation)
    assert list(tmp_path.iterdir()) == []


def test_api_validates_saves_and_recovers_without_registering(monkeypatch, tmp_path):
    monkeypatch.setattr(extensions, 'EXTENSIONS_DIR', ODS / 'extensions/services')
    monkeypatch.setattr(extensions, 'USER_EXTENSIONS_DIR', tmp_path / 'user')
    monkeypatch.setattr(extensions, 'EXTENSIONS_LIBRARY_DIR', tmp_path / 'library')
    monkeypatch.setattr(extensions, 'extensions_catalog', AsyncMock(return_value={'extensions': []}))
    monkeypatch.setattr(extensions, '_extensions_lock_path', lambda: tmp_path / '.lock')
    result = asyncio.run(extensions.extension_github_save_draft(request(candidate()), api_key='owner'))
    assert result.headers['cache-control'] == 'no-store'
    receipt = json.loads(result.body)
    recovered = asyncio.run(extensions.extension_github_read_draft(receipt['draftId'], api_key='owner'))
    assert json.loads(recovered.body)['candidate'] == candidate()
    assert not (tmp_path / 'user').exists() and not (tmp_path / 'library').exists()
    with pytest.raises(extensions.HTTPException) as error:
        asyncio.run(extensions.extension_github_read_draft(receipt['draftId'], api_key='other'))
    assert error.value.status_code == 404
    monkeypatch.setattr(extensions, 'extensions_catalog', AsyncMock(return_value={'extensions': [{'id': 'apache-answer'}]}))
    with pytest.raises(extensions.HTTPException) as error:
        asyncio.run(extensions.extension_github_save_draft(request(candidate()), api_key='owner'))
    assert error.value.status_code == 409


def test_draft_evidence_rechecks_exact_commit_and_current_catalog(monkeypatch, tmp_path):
    import extension_github
    monkeypatch.setattr(extensions, 'EXTENSIONS_DIR', ODS / 'extensions/services')
    monkeypatch.setattr(extensions, 'USER_EXTENSIONS_DIR', tmp_path / 'user')
    monkeypatch.setattr(extensions, 'EXTENSIONS_LIBRARY_DIR', tmp_path / 'library')
    catalog = AsyncMock(return_value={'extensions': []})
    monkeypatch.setattr(extensions, 'extensions_catalog', catalog)
    monkeypatch.setattr(extensions, '_extensions_lock_path', lambda: tmp_path / '.lock')
    saved = asyncio.run(extensions.extension_github_save_draft(request(candidate()), api_key='owner'))
    draft_id = json.loads(saved.body)['draftId']
    inspection = AsyncMock(return_value={'commit': candidate()['commit'], 'installationStarted': False})
    monkeypatch.setattr(extension_github, 'inspect_repository', inspection)
    catalog.return_value = {'extensions': [{'id': 'apache-answer'}]}
    result = json.loads(asyncio.run(extensions.extension_github_draft_evidence(draft_id, api_key='owner')).body)
    assert inspection.call_args.kwargs['revision'] == candidate()['commit']
    assert result['validation']['valid'] is False
    assert result['licenseReviewRequired'] is True
    assert result['registered'] is False and result['runtimeVerified'] is False
    inspection.reset_mock()
    with pytest.raises(extensions.HTTPException) as error:
        asyncio.run(extensions.extension_github_draft_evidence(draft_id, api_key='other-owner'))
    assert error.value.status_code == 404
    inspection.assert_not_called()
