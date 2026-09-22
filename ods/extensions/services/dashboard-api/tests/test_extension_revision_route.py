"""Owner-bound recipe repair through the actual proposal route, without Docker."""
import asyncio
import copy
import json
import shutil
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from routers import extensions
from extension_requests import create_request, bind_proposal, read_request
from extension_recipe_drafts import save_draft
from extension_recipe_package import publish_package, verify_package, recipe_digest
from extension_installation import InstallationJournal
from test_extension_recipe_validation import candidate, ODS
from test_extension_recipe_drafts import evidence
from test_extension_recipe_package import upstream


@pytest.fixture
def repair(tmp_path, monkeypatch):
    original = candidate()
    identifier = original['manifest']['service']['id']
    library, users = tmp_path / 'library', tmp_path / 'user'
    drafts, requests = tmp_path / '.extension-recipe-drafts', tmp_path / '.extension-requests'
    operations = tmp_path / '.extension-installations'
    for directory in (library, users, drafts, requests, operations): directory.mkdir()
    draft = save_draft(drafts, 'owner', original, evidence(original))
    create_request(requests, 'owner', 'chat', 'turn', '/extensions ' + original['repository'])
    current = bind_proposal(requests, 'owner', 'chat', 'turn', original, evidence(original), draft)
    publish_package(library, original, evidence(original), upstream(original))
    installed = users / identifier
    shutil.copytree(library / identifier, installed)
    digest = extensions._extension_tree_digest(installed)
    extensions._write_library_receipt(installed, source_digest=digest, installed_digest=digest)
    (installed / '.env').write_text('OWNER_SETTING=retained')
    (installed / 'data').mkdir()
    (installed / 'data/database').write_bytes(b'owner database')
    journal = InstallationJournal(operations / 'journal.json')
    journal.records[identifier] = {'action': 'install', 'state': 'accepted', 'operationId': 'a' * 32}
    journal.save()
    receipt = {'service_id': identifier, 'operation_id': 'a' * 32, 'state': 'failed'}
    monkeypatch.setattr(extensions, '_extensions_lock_path', lambda: tmp_path / '.lock')
    monkeypatch.setattr(extensions, 'EXTENSIONS_DIR', ODS / 'extensions/services')
    monkeypatch.setattr(extensions, 'USER_EXTENSIONS_DIR', users)
    monkeypatch.setattr(extensions, 'EXTENSIONS_LIBRARY_DIR', library)
    monkeypatch.setattr(extensions, 'extensions_catalog', AsyncMock(return_value={'extensions': [{'id': identifier}]}))
    monkeypatch.setattr(extensions, 'request_agent_json', lambda *args, **kwargs: {'operation': receipt.copy()})
    monkeypatch.setattr(extensions, '_read_progress', lambda sid: {'status': 'error', 'operation_id': 'a' * 32})
    monkeypatch.setattr(extensions, '_call_agent_install', lambda *args, **kwargs: pytest.fail('Proposal must not install'))
    updated = copy.deepcopy(original)
    updated['commit'] = 'b' * 40
    monkeypatch.setattr('extension_github.inspect_repository', AsyncMock(return_value={
        **upstream(updated), 'existingExtensionIds': [identifier]}))

    def send(value=updated, owner='owner'):
        async def receive():
            return {'type': 'http.request', 'body': json.dumps({'chatId': 'chat', 'requestId': 'turn',
                'candidate': value}).encode(), 'more_body': False}
        request = Request({'type': 'http', 'method': 'POST', 'headers': []}, receive)
        return json.loads(asyncio.run(extensions.extension_github_request_proposal(request, api_key=owner)).body)

    return dict(send=send, original=original, updated=updated, installed=installed, library=library,
        identifier=identifier, requests=requests, journal=journal, receipt=receipt, current=current)


def test_failed_recipe_can_be_revised_and_replayed_without_installing(repair):
    result = repair['send']()
    assert result['proposal']['recipeDigest'] == recipe_digest(repair['updated'])
    assert result['installationStarted'] is False
    verify_package(repair['library'] / repair['identifier'], repair['updated'])
    assert repair['identifier'] not in InstallationJournal(repair['journal'].path).records
    assert (repair['installed'] / '.env').read_text() == 'OWNER_SETTING=retained'
    assert (repair['installed'] / 'data/database').read_bytes() == b'owner database'
    assert repair['send']() == result


@pytest.mark.parametrize('condition', ['running', 'uncertain', 'succeeded', 'wrong-owner', 'local-edit'])
def test_revision_requires_exact_failure_and_unchanged_owner_definition(repair, condition):
    if condition in ('running', 'uncertain', 'succeeded'):
        repair['receipt']['state'] = condition
    if condition == 'local-edit':
        (repair['installed'] / 'compose.yaml').write_text('owner edit')
    before = {p.name: p.read_bytes() for p in repair['installed'].iterdir() if p.is_file()}
    with pytest.raises(extensions.HTTPException) as failure:
        repair['send'](owner='other' if condition == 'wrong-owner' else 'owner')
    assert failure.value.status_code == 409
    assert before == {p.name: p.read_bytes() for p in repair['installed'].iterdir() if p.is_file()}
    assert repair['identifier'] in InstallationJournal(repair['journal'].path).records
    verify_package(repair['library'] / repair['identifier'], repair['original'])


def test_lost_reply_after_binding_recovers_files_and_retires_only_original_attempt(repair, monkeypatch):
    import extension_requests
    original_bind = extension_requests.bind_proposal
    def lose_reply(*args, **kwargs):
        original_bind(*args, **kwargs)
        raise OSError('reply lost after durable binding')
    with monkeypatch.context() as interrupted:
        interrupted.setattr(extension_requests, 'bind_proposal', lose_reply)
        with pytest.raises(extensions.HTTPException): repair['send']()
    assert repair['identifier'] in InstallationJournal(repair['journal'].path).records
    result = repair['send']()
    assert result['proposal']['recipeDigest'] == recipe_digest(repair['updated'])
    assert repair['identifier'] not in InstallationJournal(repair['journal'].path).records
    assert (repair['installed'] / 'data/database').read_bytes() == b'owner database'


@pytest.mark.parametrize('progress', [None, {'status': 'pulling'},
    {'status': 'error', 'operation_id': 'c' * 32}])
def test_an_old_failure_cannot_revise_a_newer_or_unknown_attempt(repair, monkeypatch, progress):
    monkeypatch.setattr(extensions, '_read_progress', lambda sid: progress)
    with pytest.raises(extensions.HTTPException) as failure:
        repair['send']()
    assert failure.value.status_code == 409
    verify_package(repair['library'] / repair['identifier'], repair['original'])
    assert repair['identifier'] in InstallationJournal(repair['journal'].path).records
