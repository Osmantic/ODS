"""Owner-bound recipe repair through the actual proposal route, without Docker."""
import asyncio
import copy
import json
from pathlib import Path
import shutil
import time
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from routers import extensions
from extension_requests import (TTL_SECONDS, _identity, create_request, bind_proposal, read_request,
                                cancel_request)
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
    create_request(requests, 'owner', 'chat', 'turn', '/extensions install ' + original['repository'])
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

    def send(value=updated, owner='owner', request_id='turn', recovery_from=None):
        async def receive():
            payload = {'chatId': 'chat', 'requestId': request_id, 'candidate': value}
            if recovery_from is not None:
                payload['recoveryFrom'] = recovery_from
            return {'type': 'http.request', 'body': json.dumps(payload).encode(), 'more_body': False}
        request = Request({'type': 'http', 'method': 'POST', 'headers': []}, receive)
        return json.loads(asyncio.run(extensions.extension_github_request_proposal(request, api_key=owner)).body)

    return dict(send=send, original=original, updated=updated, installed=installed, library=library,
        identifier=identifier, requests=requests, journal=journal, receipt=receipt, current=current,
        data_dir=tmp_path)


def failed_progress_file(repair, monkeypatch, **changes):
    monkeypatch.setattr(extensions, 'DATA_DIR', repair['data_dir'])
    directory = repair['data_dir'] / 'extension-progress'
    directory.mkdir(exist_ok=True)
    path = directory / (repair['identifier'] + '.json')
    progress = {'service_id': repair['identifier'], 'status': 'error',
                'operation_id': 'a' * 32, 'error': 'old build failure'}
    progress.update(changes)
    path.write_text(json.dumps(progress), encoding='utf-8')
    return path


def test_failed_recipe_can_be_revised_and_replayed_without_installing(repair):
    result = repair['send']()
    assert result['proposal']['recipeDigest'] == recipe_digest(repair['updated'])
    assert result['installationStarted'] is False
    verify_package(repair['library'] / repair['identifier'], repair['updated'])
    assert repair['identifier'] not in InstallationJournal(repair['journal'].path).records
    assert (repair['installed'] / '.env').read_text() == 'OWNER_SETTING=retained'
    assert (repair['installed'] / 'data/database').read_bytes() == b'owner database'
    assert repair['send']() == result


def test_revision_clears_only_retired_failed_progress_and_keeps_host_receipt(repair, monkeypatch):
    progress = failed_progress_file(repair, monkeypatch)
    host_receipt = repair['receipt'].copy()
    repair['send']()
    assert not progress.exists()
    assert repair['receipt'] == host_receipt
    assert repair['identifier'] not in InstallationJournal(repair['journal'].path).records


@pytest.mark.parametrize('changes', [
    {'operation_id': 'b' * 32}, {'status': 'pulling'}, {'service_id': 'another'}])
def test_revision_retains_journal_when_progress_no_longer_matches(repair, monkeypatch, changes):
    progress = failed_progress_file(repair, monkeypatch, **changes)
    previous = progress.read_bytes()
    with pytest.raises(extensions.HTTPException) as failure:
        repair['send']()
    assert failure.value.status_code == 409
    assert progress.read_bytes() == previous
    assert repair['identifier'] not in InstallationJournal(repair['journal'].path).records
    assert (repair['journal'].path.parent / (repair['current']['id'] + '.revision.json')).exists()


def test_progress_cleanup_failure_replays_without_losing_old_evidence(repair, monkeypatch):
    progress = failed_progress_file(repair, monkeypatch)
    original_unlink = Path.unlink

    def unavailable(path, *args, **kwargs):
        if path == progress:
            raise PermissionError('simulated progress lock')
        return original_unlink(path, *args, **kwargs)

    with monkeypatch.context() as interrupted:
        interrupted.setattr(Path, 'unlink', unavailable)
        with pytest.raises(extensions.HTTPException) as failure:
            repair['send']()
        assert failure.value.status_code == 409
    journal_path = repair['journal'].path.parent / (repair['current']['id'] + '.revision.json')
    assert journal_path.exists() and progress.exists()
    assert repair['receipt']['state'] == 'failed'
    repair['send']()
    assert not journal_path.exists() and not progress.exists()


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


def test_expired_failed_recipe_recovers_into_explicit_new_request(repair):
    previous_path = repair['requests'] / (repair['current']['id'] + '.json')
    previous = json.loads(previous_path.read_text())
    previous['createdAt'] = int(time.time()) - TTL_SECONDS - 10
    previous['expiresAt'] = previous['createdAt'] + TTL_SECONDS
    previous_path.write_text(json.dumps(previous))
    previous_binding = repair['current']['proposal']
    assert read_request(repair['requests'], 'owner', 'chat', 'turn')['state'] == 'expired'

    create_request(repair['requests'], 'owner', 'chat', 'fresh',
                   '/extensions install ' + repair['original']['repository'])
    assert read_request(repair['requests'], 'owner', 'chat', 'turn')['state'] == 'expired'
    recovery = {'chatId': 'chat', 'requestId': 'turn'}
    result = repair['send'](request_id='fresh', recovery_from=recovery)
    assert result['proposal']['recipeDigest'] == recipe_digest(repair['updated'])
    assert result['installationStarted'] is False
    assert read_request(repair['requests'], 'owner', 'chat', 'turn')['proposal'] == previous_binding
    assert repair['identifier'] not in InstallationJournal(repair['journal'].path).records
    verify_package(repair['library'] / repair['identifier'], repair['updated'])
    assert (repair['installed'] / '.env').read_text() == 'OWNER_SETTING=retained'
    assert (repair['installed'] / 'data/database').read_bytes() == b'owner database'
    assert repair['send'](request_id='fresh', recovery_from=recovery) == result


@pytest.mark.parametrize('condition', ['no-recovery', 'wrong-old-id', 'cancelled-old',
    'wrong-repository', 'wrong-failure', 'wrong-owner'])
def test_expired_recovery_rejects_unverified_carry_forward(repair, condition):
    previous_path = repair['requests'] / (repair['current']['id'] + '.json')
    previous = json.loads(previous_path.read_text())
    previous['createdAt'] = int(time.time()) - TTL_SECONDS - 10
    previous['expiresAt'] = previous['createdAt'] + TTL_SECONDS
    previous_path.write_text(json.dumps(previous))
    if condition == 'cancelled-old':
        cancel_request(repair['requests'], 'owner', 'chat', 'turn')
    if condition == 'wrong-failure':
        repair['receipt']['state'] = 'running'
    repository = (repair['original']['repository'] if condition != 'wrong-repository'
                  else 'https://github.com/different/repository')
    create_request(repair['requests'], 'owner', 'chat', 'fresh', '/extensions install ' + repository)
    if condition == 'wrong-owner':
        create_request(repair['requests'], 'other', 'chat', 'fresh',
                       '/extensions install ' + repository)
    recovery = None if condition == 'no-recovery' else {'chatId': 'chat',
        'requestId': 'missing' if condition == 'wrong-old-id' else 'turn'}
    with pytest.raises(extensions.HTTPException) as failure:
        repair['send'](owner='other' if condition == 'wrong-owner' else 'owner',
                       request_id='fresh', recovery_from=recovery)
    assert failure.value.status_code in (409, 422)
    assert read_request(repair['requests'], 'owner', 'chat', 'fresh').get('proposal') is None
    verify_package(repair['library'] / repair['identifier'], repair['original'])
    assert repair['identifier'] in InstallationJournal(repair['journal'].path).records


def test_expired_recovery_replays_after_binding_response_was_lost(repair, monkeypatch):
    import extension_requests

    previous_path = repair['requests'] / (repair['current']['id'] + '.json')
    previous = json.loads(previous_path.read_text())
    previous['createdAt'] = int(time.time()) - TTL_SECONDS - 10
    previous['expiresAt'] = previous['createdAt'] + TTL_SECONDS
    previous_path.write_text(json.dumps(previous))
    create_request(repair['requests'], 'owner', 'chat', 'fresh',
                   '/extensions install ' + repair['original']['repository'])
    original_adopt = extension_requests.adopt_expired_proposal

    def lose_reply(*args, **kwargs):
        original_adopt(*args, **kwargs)
        raise OSError('reply lost after durable adoption')

    with monkeypatch.context() as interrupted:
        interrupted.setattr(extension_requests, 'adopt_expired_proposal', lose_reply)
        with pytest.raises(extensions.HTTPException):
            repair['send'](request_id='fresh', recovery_from={'chatId': 'chat', 'requestId': 'turn'})
    assert repair['identifier'] in InstallationJournal(repair['journal'].path).records
    assert (repair['installed'] / 'data/database').read_bytes() == b'owner database'
    with pytest.raises(ValueError, match='Pending recipe revision'):
        create_request(repair['requests'], 'owner', 'chat', 'next',
                       '/extensions install ' + repair['original']['repository'])
    assert read_request(repair['requests'], 'owner', 'chat', 'fresh')['state'] == 'pending'
    with pytest.raises(ValueError, match='Pending recipe revision'):
        extensions._advance_extension_installation(repair['identifier'], 'owner', None,
            {'chatId': 'chat', 'requestId': 'fresh'}, _identity('owner', 'chat', 'fresh')[1])
    result = repair['send'](request_id='fresh')
    assert result['proposal']['recipeDigest'] == recipe_digest(repair['updated'])
    assert repair['identifier'] not in InstallationJournal(repair['journal'].path).records
