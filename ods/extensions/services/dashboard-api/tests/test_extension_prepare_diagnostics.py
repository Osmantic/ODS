"""Request-bound preparation failures must reach the model as finite reasons."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from extension_recipe_drafts import save_draft
from extension_requests import bind_proposal, cancel_request, create_request
from routers import extensions
from test_extension_recipe_drafts import evidence
from test_extension_recipe_validation import ODS, candidate


def _request():
    async def receive():
        return {'type': 'http.request',
                'body': json.dumps({'chatId': 'chat', 'requestId': 'turn'}).encode(),
                'more_body': False}
    return Request({'type': 'http', 'method': 'POST', 'headers': []}, receive)


def _accepted_request(monkeypatch, tmp_path):
    proposal = candidate()
    drafts = tmp_path / '.extension-recipe-drafts'; drafts.mkdir()
    requests = tmp_path / '.extension-requests'; requests.mkdir()
    library = tmp_path / 'library'; library.mkdir()
    draft = save_draft(drafts, 'owner', proposal, evidence(proposal))
    create_request(requests, 'owner', 'chat', 'turn', '/extensions install ' + proposal['repository'])
    bind_proposal(requests, 'owner', 'chat', 'turn', proposal, evidence(proposal), draft)
    monkeypatch.setattr(extensions, '_extensions_lock_path', lambda: tmp_path / '.lock')
    monkeypatch.setattr(extensions, 'EXTENSIONS_LIBRARY_DIR', library)
    monkeypatch.setattr(extensions, 'USER_EXTENSIONS_DIR', tmp_path / 'user')
    monkeypatch.setattr(extensions, 'EXTENSIONS_DIR', ODS / 'extensions/services')
    monkeypatch.setattr(extensions, 'extensions_catalog', AsyncMock(return_value={'extensions': []}))
    return proposal, library


@pytest.mark.parametrize('reason, failure_type', [
    ('license_review_required', 'license'),
    ('recipe_inspection_required', 'recipe'),
    ('repository_evidence_unavailable', 'repository'),
])
def test_preparation_failure_is_scoped_and_does_not_claim_installation(
        monkeypatch, tmp_path, reason, failure_type):
    from extension_recipe_package import LicenseEvidenceError

    proposal, library = _accepted_request(monkeypatch, tmp_path)
    upstream = {'repository': proposal['repository'], 'commit': proposal['commit'],
                'existingExtensionIds': [], 'evidenceScope': 'repository-documents-at-commit',
                'licenseIdentifier': 'Apache-2.0', 'licenseText': 'License at pinned commit'}
    if failure_type == 'repository':
        monkeypatch.setattr('extension_github.inspect_repository',
                            AsyncMock(side_effect=ValueError('private upstream detail')))
    else:
        monkeypatch.setattr('extension_github.inspect_repository', AsyncMock(return_value=upstream))
        error = (LicenseEvidenceError('private upstream detail') if failure_type == 'license'
                 else ValueError('private upstream detail'))

        def reject(*_args, **_kwargs):
            raise error
        monkeypatch.setattr('extension_recipe_package.publish_package', reject)

    with pytest.raises(extensions.HTTPException) as rejected:
        asyncio.run(extensions.extension_github_prepare_request(_request(), api_key='owner'))
    assert rejected.value.status_code == 409
    assert rejected.value.detail == {
        'schemaVersion': 1, 'kind': 'ods-extension-request-preparation-rejected',
        'chatId': 'chat', 'requestId': 'turn', 'reason': reason,
        'installationStarted': False,
    }
    assert 'private upstream detail' not in json.dumps(rejected.value.detail)
    assert not list(library.iterdir())


def test_request_cancelled_during_repository_lookup_cannot_publish(monkeypatch, tmp_path):
    proposal, library = _accepted_request(monkeypatch, tmp_path)

    async def lookup(*_args, **_kwargs):
        cancel_request(tmp_path / '.extension-requests', 'owner', 'chat', 'turn')
        return {'repository': proposal['repository'], 'commit': proposal['commit'],
                'existingExtensionIds': [], 'evidenceScope': 'repository-documents-at-commit',
                'licenseIdentifier': 'Apache-2.0', 'licenseText': 'License at pinned commit'}

    monkeypatch.setattr('extension_github.inspect_repository', lookup)
    with pytest.raises(extensions.HTTPException) as rejected:
        asyncio.run(extensions.extension_github_prepare_request(_request(), api_key='owner'))
    assert rejected.value.status_code == 409
    assert rejected.value.detail['reason'] == 'request_changed'
    assert rejected.value.detail['installationStarted'] is False
    assert not list(library.iterdir())
