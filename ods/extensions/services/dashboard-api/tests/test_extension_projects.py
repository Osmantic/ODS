import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from extension_projects import associate_project, read_projects, validate_project
from routers import extensions


@pytest.mark.parametrize('value', ['../outside', 'Playground/../other', 'Playground//a',
                                   'C:\\Playground\\a', '/Playground/a', 'Playground/a/b'])
def test_project_references_are_portable_identifiers_not_filesystem_paths(value):
    with pytest.raises(ValueError):
        validate_project(value)


def test_association_is_idempotent_and_preserves_other_projects(tmp_path):
    path = tmp_path / 'projects.json'
    associate_project(path, 'Playground/first')
    before = path.read_bytes()
    assert associate_project(path, 'Playground/first') == ['Playground/first']
    assert path.read_bytes() == before
    assert associate_project(path, 'Playground/second') == ['Playground/first', 'Playground/second']


def test_corrupt_references_are_not_silently_replaced(tmp_path):
    path = tmp_path / 'projects.json'
    path.write_text('{broken')
    with pytest.raises(ValueError):
        associate_project(path, 'Playground/project')
    assert path.read_text() == '{broken'


def test_reference_storage_is_scoped_to_owner_and_extension(tmp_path, monkeypatch):
    monkeypatch.setattr(extensions, '_extensions_lock_path', lambda: tmp_path / 'lock')
    extensions._extension_project_references('demo', 'owner-one', 'Playground/first')
    assert extensions._extension_project_references('demo', 'owner-one') == ['Playground/first']
    assert extensions._extension_project_references('other', 'owner-one') == []
    assert extensions._extension_project_references('demo', 'owner-two') == []
    assert 'owner-one' not in ''.join(p.name for p in tmp_path.rglob('*'))


def request():
    async def receive():
        return {'type': 'http.request', 'body': json.dumps({'project': 'Playground/first'}).encode(), 'more_body': False}
    return Request({'type': 'http'}, receive)


@pytest.mark.parametrize('state', ['installing', 'not_installed', 'unhealthy'])
def test_unconfirmed_installation_cannot_create_association(tmp_path, monkeypatch, state):
    monkeypatch.setattr(extensions, '_extensions_lock_path', lambda: tmp_path / 'lock')
    monkeypatch.setattr(extensions, 'extension_detail', AsyncMock(return_value={'status': state}))
    with pytest.raises(HTTPException) as error:
        asyncio.run(extensions.extension_associate_project('demo', request(), 'owner'))
    assert error.value.status_code == 409
    assert not list(tmp_path.glob('**/*.json'))


def test_ready_extension_association_does_not_modify_project_files(tmp_path, monkeypatch):
    monkeypatch.setattr(extensions, '_extensions_lock_path', lambda: tmp_path / 'lock')
    monkeypatch.setattr(extensions, 'extension_detail', AsyncMock(return_value={'status': 'enabled'}))
    result = asyncio.run(extensions.extension_associate_project('demo', request(), 'owner'))
    assert result == {'extensionId': 'demo', 'projects': ['Playground/first'], 'scope': 'project-association'}
    assert not (tmp_path / 'Playground').exists()
