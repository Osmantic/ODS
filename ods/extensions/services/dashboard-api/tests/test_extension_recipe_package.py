import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from extension_recipe_package import publish_package, verify_package
from extension_recipe_drafts import save_draft
from test_extension_recipe_drafts import evidence
from test_extension_recipe_validation import candidate, ODS
from routers import extensions
from starlette.requests import Request
from extension_requests import create_request, bind_proposal, cancel_request


@pytest.mark.parametrize('interruption', [None, 'cancel', 'replace'])
def test_request_preparation_rechecks_scope_after_upstream_lookup(monkeypatch, tmp_path, interruption):
    proposal = candidate()
    drafts = tmp_path / '.extension-recipe-drafts'; drafts.mkdir()
    requests = tmp_path / '.extension-requests'; requests.mkdir()
    library = tmp_path / 'library'; library.mkdir()
    draft = save_draft(drafts, 'owner', proposal, evidence(proposal))
    create_request(requests, 'owner', 'chat', 'turn', '/extensions ' + proposal['repository'])
    bind_proposal(requests, 'owner', 'chat', 'turn', proposal, evidence(proposal), draft)
    monkeypatch.setattr(extensions, '_extensions_lock_path', lambda: tmp_path / '.lock')
    monkeypatch.setattr(extensions, 'EXTENSIONS_LIBRARY_DIR', library)
    monkeypatch.setattr(extensions, 'USER_EXTENSIONS_DIR', tmp_path / 'user')
    monkeypatch.setattr(extensions, 'EXTENSIONS_DIR', ODS / 'extensions/services')
    monkeypatch.setattr(extensions, 'extensions_catalog', AsyncMock(return_value={'extensions': []}))

    async def lookup(*args, **kwargs):
        if interruption == 'cancel':
            cancel_request(requests, 'owner', 'chat', 'turn')
        elif interruption == 'replace':
            create_request(requests, 'owner', 'chat', 'new-turn', '/extensions https://github.com/other/repo')
        return upstream(proposal)
    monkeypatch.setattr('extension_github.inspect_repository', lookup)

    def request():
        async def receive():
            return {'type': 'http.request', 'body': b'{"chatId":"chat","requestId":"turn"}', 'more_body': False}
        return Request({'type': 'http', 'method': 'POST', 'headers': []}, receive)

    with pytest.raises(extensions.HTTPException) as failure:
        asyncio.run(extensions.extension_github_prepare_request(request(), api_key='other-owner'))
    assert failure.value.status_code == 409 and not list(library.iterdir())
    if interruption:
        with pytest.raises(extensions.HTTPException) as failure:
            asyncio.run(extensions.extension_github_prepare_request(request(), api_key='owner'))
        assert failure.value.status_code == 409 and not list(library.iterdir())
    else:
        result = json.loads(asyncio.run(extensions.extension_github_prepare_request(request(), api_key='owner')).body)
        assert result['kind'] == 'ods-extension-request-preparation'
        assert result['chatId'] == 'chat' and result['requestId'] == 'turn'
        assert result['draftId'] == draft['draftId']
        assert result['extensionId'] == 'apache-answer' and result['state'] == 'available'
        assert result['installationStarted'] is False
        assert (library / 'apache-answer/manifest.yaml').is_file()
    assert not (tmp_path / 'user').exists()


def upstream(proposal):
    return {'repository': proposal['repository'], 'commit': proposal['commit'],
            'existingExtensionIds': [], 'evidenceScope': 'repository-documents-at-commit',
            'licenseIdentifier': 'Apache-2.0', 'licenseText': 'License evidence from pinned revision'}


def test_publish_is_atomic_idempotent_and_does_not_install(tmp_path):
    proposal = candidate()
    first = publish_package(tmp_path, proposal, evidence(proposal), upstream(proposal))
    before = {p.name: p.read_bytes() for p in (tmp_path / 'apache-answer').iterdir()}
    assert publish_package(tmp_path, proposal, evidence(proposal), upstream(proposal)) == first
    assert before == {p.name: p.read_bytes() for p in (tmp_path / 'apache-answer').iterdir()}
    assert first['state'] == 'available' and first['installationStarted'] is False
    assert first['registered'] is False
    assert [p.name for p in tmp_path.iterdir()] == ['apache-answer']


@pytest.mark.parametrize('change', [{'commit': 'b' * 40}, {'repository': 'https://github.com/other/repo'},
    {'existingExtensionIds': ['other']}, {'licenseIdentifier': 'NOASSERTION'}, {'licenseText': None}])
def test_stale_conflicting_or_unreviewed_evidence_cannot_publish(tmp_path, change):
    proposal = candidate()
    with pytest.raises(ValueError):
        publish_package(tmp_path, proposal, evidence(proposal), {**upstream(proposal), **change})
    assert not list(tmp_path.iterdir())


def test_changed_files_cannot_be_repaired_by_repeat_preparation(tmp_path):
    proposal = candidate()
    publish_package(tmp_path, proposal, evidence(proposal), upstream(proposal))
    path = tmp_path / 'apache-answer/compose.yaml'
    path.write_text('services: {}')
    with pytest.raises(ValueError):
        publish_package(tmp_path, proposal, evidence(proposal), upstream(proposal))
    assert path.read_text() == 'services: {}'


def test_extra_script_or_changed_digest_is_rejected(tmp_path):
    proposal = candidate()
    publish_package(tmp_path, proposal, evidence(proposal), upstream(proposal))
    (tmp_path / 'apache-answer/setup.sh').write_text('unexpected')
    with pytest.raises(ValueError):
        verify_package(tmp_path / 'apache-answer', proposal)


def test_prepared_recipe_is_discoverable_and_stages_without_curated_privileges(monkeypatch, tmp_path):
    proposal = candidate()
    library = tmp_path / 'library'; library.mkdir()
    publish_package(library, proposal, evidence(proposal), upstream(proposal))
    monkeypatch.setattr(extensions, 'EXTENSION_CATALOG', [])
    monkeypatch.setattr(extensions, 'EXTENSIONS_LIBRARY_DIR', library)
    monkeypatch.setattr(extensions, 'USER_EXTENSIONS_DIR', tmp_path / 'user')
    monkeypatch.setattr(extensions, 'EXTENSIONS_DIR', ODS / 'extensions/services')
    assert extensions._current_extension_catalog()[0]['id'] == 'apache-answer'
    observed = []
    original = extensions._scan_compose_content
    def scan(path, trusted=False):
        observed.append(trusted)
        return original(path, trusted=trusted)
    monkeypatch.setattr(extensions, '_scan_compose_content', scan)
    with extensions._staged_library_extension('apache-answer', tmp_path / 'user/apache-answer') as (staged, digest):
        assert (staged / 'compose.yaml').is_file()
        assert digest
    assert observed == [False]
    assert not (tmp_path / 'user/apache-answer').exists()


def test_prepare_route_publishes_configuration_and_retry_does_not_overwrite(monkeypatch, tmp_path):
    proposal = candidate()
    drafts = tmp_path / '.extension-recipe-drafts'; drafts.mkdir()
    library = tmp_path / 'library'; library.mkdir()
    receipt = save_draft(drafts, 'owner', proposal, evidence(proposal))
    monkeypatch.setattr(extensions, '_extensions_lock_path', lambda: tmp_path / '.lock')
    monkeypatch.setattr(extensions, 'EXTENSIONS_LIBRARY_DIR', library)
    monkeypatch.setattr(extensions, 'USER_EXTENSIONS_DIR', tmp_path / 'user')
    monkeypatch.setattr(extensions, 'EXTENSIONS_DIR', ODS / 'extensions/services')
    monkeypatch.setattr(extensions, 'extensions_catalog', AsyncMock(return_value={'extensions': []}))
    lookup = AsyncMock(return_value=upstream(proposal))
    monkeypatch.setattr('extension_github.inspect_repository', lookup)
    async def run():
        first = await extensions.extension_github_prepare_draft(receipt['draftId'], api_key='owner')
        second = await extensions.extension_github_prepare_draft(receipt['draftId'], api_key='owner')
        return first, second
    first, second = asyncio.run(run())
    assert json.loads(first.body) == json.loads(second.body)
    assert json.loads(first.body)['state'] == 'available'
    assert first.headers['cache-control'] == 'no-store'
    assert lookup.call_args.kwargs['revision'] == proposal['commit']
    assert not (tmp_path / 'user').exists()
