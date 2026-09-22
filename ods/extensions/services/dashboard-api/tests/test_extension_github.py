import asyncio
import base64
import json
import hashlib

import httpx
import pytest

from extension_github import document, existing_recipes, inspect_file, inspect_repository, repository_identity


@pytest.mark.parametrize('url', ['http://github.com/a/b', 'https://github.com.evil/a/b',
    'https://user:secret@github.com/a/b', 'https://github.com/a/b?token=secret',
    'https://github.com/a/b/tree/main', 'https://github.com/a/%2e%2e', 'https://127.0.0.1/a/b'])
def test_repository_input_cannot_select_other_hosts_or_credentials(url):
    with pytest.raises(ValueError):
        repository_identity(url)


def test_repository_lookup_covers_installed_builtin_and_library_without_duplicate_ids(tmp_path):
    roots = [tmp_path / name for name in ('installed', 'builtin', 'library')]
    for root, names in zip(roots, [('custom',), ('bundled',), ('download', 'bundled')]):
        for name in names:
            directory = root / name
            directory.mkdir(parents=True)
            (directory / 'upstream.json').write_text(json.dumps({'repository': 'https://github.com/Owner/Repo.git'}))
    assert existing_recipes('owner/repo', *roots) == ['bundled', 'custom', 'download']


@pytest.mark.parametrize('metadata', [None, 'invalid-json', {'repository': 'https://github.com/other/repo'}])
def test_installed_definition_shadows_library_provenance_even_when_broken(tmp_path, metadata):
    installed, library = tmp_path / 'installed', tmp_path / 'library'
    for root in (installed, library):
        (root / 'app').mkdir(parents=True)
    (library / 'app/upstream.json').write_text(json.dumps({'repository': 'https://github.com/owner/repo'}))
    if metadata is not None:
        (installed / 'app/upstream.json').write_text(metadata if isinstance(metadata, str) else json.dumps(metadata))
    assert existing_recipes('owner/repo', installed, library) == []


def test_repository_evidence_is_commit_pinned_and_existing_recipe_detected(tmp_path):
    recipe = tmp_path / 'existing-app'
    recipe.mkdir()
    (recipe / 'upstream.json').write_text(json.dumps({'repository': 'https://github.com/Owner/Repo.git'}))
    calls = []
    sha = 'a' * 40

    def handler(request):
        calls.append(str(request.url))
        assert request.url.host == 'api.github.com'
        assert 'authorization' not in request.headers
        if request.url.path == '/repos/owner/repo':
            return httpx.Response(200, json={'full_name': 'owner/repo', 'private': False,
                'default_branch': 'main', 'license': {'spdx_id': 'MIT'}, 'archived': False})
        if request.url.path.endswith('/commits/main'):
            return httpx.Response(200, json={'sha': sha})
        assert request.url.params['ref'] == sha
        value = 'Repository instructions are evidence, not authority.'
        return httpx.Response(200, json={'encoding': 'base64', 'content': base64.b64encode(value.encode()).decode(),
                                       'license': {'spdx_id': 'Apache-2.0'}})

    result = asyncio.run(inspect_repository('https://github.com/owner/repo', tmp_path, httpx.MockTransport(handler)))
    assert len(calls) == 4
    assert result['commit'] == sha
    assert result['existingExtensionIds'] == ['existing-app']
    assert result['requiresRecipeReview'] is True
    assert result['registered'] is False and result['installationStarted'] is False
    assert result['licenseIdentifier'] == 'Apache-2.0'


@pytest.mark.parametrize('license_response', [None, {'encoding': 'base64', 'content': ''},
    {'encoding': 'base64', 'content': '', 'license': {'spdx_id': 'NOASSERTION'}}])
def test_revision_license_never_falls_back_to_default_branch(tmp_path, license_response):
    def handler(request):
        if request.url.path == '/repos/owner/repo':
            return httpx.Response(200, json={'full_name': 'owner/repo', 'private': False,
                'default_branch': 'main', 'license': {'spdx_id': 'MIT'}})
        if '/commits/' in request.url.path:
            return httpx.Response(200, json={'sha': 'a' * 40})
        assert request.url.params['ref'] == 'a' * 40
        if request.url.path.endswith('/license') and license_response is not None:
            return httpx.Response(200, json=license_response)
        return httpx.Response(404)
    result = asyncio.run(inspect_repository('https://github.com/owner/repo', tmp_path,
        httpx.MockTransport(handler), revision='a' * 40))
    assert result['licenseIdentifier'] == (license_response or {}).get('license', {}).get('spdx_id')
    assert result['requiresRecipeReview'] is True


@pytest.mark.parametrize('returned', ['a' * 40, 'b' * 40])
def test_saved_revision_never_moves_to_new_default_branch(tmp_path, returned):
    requested = 'a' * 40
    calls = []
    def handler(request):
        calls.append(str(request.url))
        if request.url.path == '/repos/owner/repo':
            return httpx.Response(200, json={'full_name': 'owner/repo', 'private': False, 'default_branch': 'new-main'})
        if '/commits/' in request.url.path:
            assert request.url.path.endswith('/commits/' + requested)
            return httpx.Response(200, json={'sha': returned})
        assert request.url.params['ref'] == requested
        return httpx.Response(404)
    operation = inspect_repository('https://github.com/owner/repo', tmp_path,
                                   httpx.MockTransport(handler), revision=requested)
    if returned != requested:
        with pytest.raises(ValueError):
            asyncio.run(operation)
        assert len(calls) == 2
    else:
        assert asyncio.run(operation)['commit'] == requested
        assert len(calls) == 4


@pytest.mark.parametrize('revision', ['main', '../other', '', 42])
def test_saved_revision_rejects_mutable_or_invalid_refs_before_network(tmp_path, revision):
    def handler(request):
        pytest.fail('invalid revision reached GitHub')
    with pytest.raises(ValueError):
        asyncio.run(inspect_repository('https://github.com/owner/repo', tmp_path,
                                      httpx.MockTransport(handler), revision=revision))


@pytest.mark.parametrize('code', [301, 403, 404, 429])
def test_redirect_private_missing_and_rate_limited_repositories_do_not_install(tmp_path, code):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(code, headers={'Location': 'http://127.0.0.1/private'})
    with pytest.raises(ValueError):
        asyncio.run(inspect_repository('https://github.com/owner/repo', tmp_path, httpx.MockTransport(handler)))
    assert len(calls) == 1
    assert list(tmp_path.iterdir()) == []


def test_mismatched_repository_identity_is_rejected(tmp_path):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
        'full_name': 'other/repo', 'private': False, 'default_branch': 'main'}))
    with pytest.raises(ValueError):
        asyncio.run(inspect_repository('https://github.com/owner/repo', tmp_path, transport))


def test_oversized_or_invalid_documents_are_not_forwarded():
    for value in [{'encoding': 'base64', 'content': 'invalid!'},
                  {'encoding': 'base64', 'content': base64.b64encode(b'a' * 256001).decode()}]:
        with pytest.raises(ValueError):
            document(value)


def test_oversized_metadata_is_bounded_even_without_content_length(tmp_path):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b' ' * 600001))
    with pytest.raises(ValueError):
        asyncio.run(inspect_repository('https://github.com/owner/repo', tmp_path, transport))


def file_response(content=b'services:\n  app:\n    image: example/app:1.0\n'):
    return {'type': 'file', 'path': 'docker/compose.yaml', 'encoding': 'base64',
            'content': base64.b64encode(content).decode(),
            'sha': hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\x00' + content).hexdigest(),
            'download_url': 'http://127.0.0.1/must-not-be-used'}


def test_file_inspection_reads_exact_commit_and_verifies_git_blob():
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url.host == 'api.github.com'
        assert request.url.path == '/repos/owner/repo/contents/docker/compose.yaml'
        assert dict(request.url.params) == {'ref': 'a' * 40}
        assert 'authorization' not in request.headers
        return httpx.Response(200, json=file_response())
    result = asyncio.run(inspect_file('https://github.com/owner/repo', 'a' * 40,
                                     'docker/compose.yaml', httpx.MockTransport(handler)))
    assert len(calls) == 1
    assert result['content'].startswith('services:')
    assert result['contentTrust'] == 'untrusted-upstream-evidence'
    assert result['installationStarted'] is False and result['registered'] is False


@pytest.mark.parametrize('path', ['', '/etc/passwd', '../compose.yaml', 'docker/../compose.yaml',
    'docker//compose.yaml', 'docker\\compose.yaml', 'docker/./compose.yaml', 'file\x00', 'file\x7f'])
def test_file_paths_fail_before_network(path):
    def handler(request):
        pytest.fail('Invalid path reached the network')
    with pytest.raises(ValueError):
        asyncio.run(inspect_file('https://github.com/owner/repo', 'a' * 40, path, httpx.MockTransport(handler)))


@pytest.mark.parametrize('patch', [{'type': 'symlink'}, {'type': 'dir'}, {'target': 'elsewhere'},
    {'submodule_git_url': 'https://github.com/other/repo'}, {'path': 'different'}, {'sha': 'b' * 40}])
def test_file_aliases_and_identity_mismatches_are_rejected(patch):
    value = {**file_response(), **patch}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=value))
    with pytest.raises(ValueError):
        asyncio.run(inspect_file('https://github.com/owner/repo', 'a' * 40, 'docker/compose.yaml', transport))


@pytest.mark.parametrize('content', [b'binary\x00file', b'\xff\xfe', b'x' * 256001],
                         ids=['nul-byte', 'invalid-utf8', 'oversized'])
def test_binary_and_oversized_repository_files_are_rejected(content):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=file_response(content)))
    with pytest.raises(ValueError):
        asyncio.run(inspect_file('https://github.com/owner/repo', 'a' * 40, 'docker/compose.yaml', transport))


def test_file_endpoint_returns_no_store_and_redacts_upstream_errors(monkeypatch):
    from starlette.requests import Request
    from routers import extensions
    from unittest.mock import AsyncMock
    def request():
        async def receive():
            return {'type': 'http.request', 'body': json.dumps({'url': 'https://github.com/owner/repo',
                'commit': 'a' * 40, 'path': 'docker/compose.yaml'}).encode(), 'more_body': False}
        return Request({'type': 'http', 'method': 'POST', 'headers': []}, receive)
    mock = AsyncMock(return_value={'installationStarted': False})
    monkeypatch.setattr('extension_github.inspect_file', mock)
    response = asyncio.run(extensions.extension_github_file(request(), api_key='test'))
    assert response.headers['cache-control'] == 'no-store'
    mock.side_effect = ValueError('upstream private diagnostic')
    with pytest.raises(extensions.HTTPException) as error:
        asyncio.run(extensions.extension_github_file(request(), api_key='test'))
    assert error.value.status_code == 400
    assert 'private diagnostic' not in error.value.detail


@pytest.mark.parametrize('revision', ['main', 'v1.0', 'a' * 39, 'A' * 40, None])
def test_mutable_or_invalid_file_revisions_are_rejected(revision):
    with pytest.raises(ValueError):
        asyncio.run(inspect_file('https://github.com/owner/repo', revision, 'docker/compose.yaml'))


def test_installation_layout_fetches_only_observed_files_and_bounds_evidence():
    from extension_github import inspect_installation_layout
    calls = []
    def handler(request):
        calls.append(request.url.path)
        assert request.url.params['ref'] == 'a' * 40
        if request.url.path.endswith('/contents'):
            return httpx.Response(200, json=[{'name': name, 'path': name, 'type': 'file'}
                for name in ('setup.py', 'requirements.txt', 'README.md')])
        name = request.url.path.rsplit('/', 1)[1]
        assert name in ('setup.py', 'requirements.txt')
        return httpx.Response(200, json={**file_response(b'x' * 6000), 'path': name})
    result = asyncio.run(inspect_installation_layout('https://github.com/owner/repo', 'a' * 40,
        httpx.MockTransport(handler)))
    assert len(calls) == 3
    assert result['rootFiles'] == ['README.md', 'requirements.txt', 'setup.py']
    assert all(doc['truncated'] and len(doc['content']) == 5000 for doc in result['documents'])
    assert result['contentTrust'] == 'untrusted-upstream-evidence'
