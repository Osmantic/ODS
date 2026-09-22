"""Bounded GitHub evidence for preparing an ODS recipe; never executes it."""
import base64
import hashlib
import json
import re
from urllib.parse import quote, urlsplit

import httpx


def repository_identity(url):
    if not isinstance(url, str) or len(url) > 512 or any(ord(c) < 33 for c in url):
        raise ValueError('Use a public GitHub repository URL')
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.netloc.lower() != 'github.com'
            or parsed.query or parsed.fragment):
        raise ValueError('Use a public GitHub repository URL')
    parts = parsed.path.strip('/').split('/')
    if len(parts) != 2:
        raise ValueError('Use the repository root URL')
    owner, repo = parts
    repo = repo[:-4] if repo.endswith('.git') else repo
    if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}', owner)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}', repo)):
        raise ValueError('Invalid GitHub repository')
    return f'{owner}/{repo}'


def existing_recipes(repository, *roots):
    """Inspect installed-first definitions; a shadowed library copy is not active."""
    matches, seen = set(), set()
    for root in roots:
        if root.is_symlink():
            raise ValueError('Recipe root requires inspection')
        for directory in root.iterdir() if root.is_dir() else []:
            if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', directory.name):
                continue
            if directory.name in seen:
                continue
            seen.add(directory.name)
            path = directory / 'upstream.json'
            # Keep the higher-priority name shadowed even if its metadata is
            # missing or invalid; never advertise the lower-priority source.
            if directory.is_symlink() or path.is_symlink():
                continue
            try:
                if not path.is_file() or path.stat().st_size > 65536:
                    continue
                value = json.loads(path.read_text(encoding='utf-8'))
                if repository_identity(value.get('repository')).lower() == repository.lower():
                    matches.add(directory.name)
            except (OSError, ValueError, AttributeError):
                continue
    return sorted(matches)


async def _github_json(client, path, optional=False):
    # No caller-selected host, credentials or redirect following. Every byte is
    # read with a bound, even when Content-Length is absent or inaccurate.
    async with client.stream('GET', f'https://api.github.com/repos/{path}') as response:
        if optional and response.status_code == 404:
            return None
        if response.status_code != 200:
            raise ValueError('GitHub evidence unavailable; repository may be private or rate-limited')
        raw = bytearray()
        async for chunk in response.aiter_bytes():
            raw.extend(chunk)
            if len(raw) > 600000:
                raise ValueError('GitHub evidence exceeds the response limit')
        return json.loads(raw)


def document(value):
    if value is None:
        return None
    if (not isinstance(value, dict) or value.get('encoding') != 'base64'
            or not isinstance(value.get('content'), str)):
        raise ValueError('Unsupported GitHub document response')
    content = base64.b64decode(''.join(value['content'].split()), validate=True)
    if len(content) > 256000:
        raise ValueError('GitHub document exceeds the content limit')
    return content.decode('utf-8')


async def inspect_file(url, commit, path, transport=None):
    """Read recipe evidence at the inspected revision, never from a download URL."""
    repository = repository_identity(url)
    if not isinstance(commit, str) or not re.fullmatch(r'[a-f0-9]{40}', commit):
        raise ValueError('An immutable repository commit is required')
    if (not isinstance(path, str) or not 1 <= len(path) <= 512
            or any(ord(c) < 32 or ord(c) == 127 for c in path)
            or '\\' in path or any(part in {'', '.', '..'} for part in path.split('/'))):
        raise ValueError('Use a repository-relative file path')
    async with httpx.AsyncClient(timeout=20, follow_redirects=False, transport=transport,
                                 headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'ODS-Extensions'}) as client:
        value = await _github_json(client, repository + '/contents/' + quote(path, safe='/') + '?ref=' + commit)
    if (not isinstance(value, dict) or value.get('type') != 'file'
            or value.get('path') != path or value.get('target') is not None
            or value.get('submodule_git_url') is not None):
        raise ValueError('Repository entry is not the requested regular file')
    content = document(value)
    if content is None or '\x00' in content:
        raise ValueError('Repository file is not a text document')
    raw = content.encode('utf-8')
    blob = hashlib.sha1(b'blob ' + str(len(raw)).encode('ascii') + b'\x00' + raw).hexdigest()
    if value.get('sha') != blob:
        raise ValueError('Repository file identity could not be verified')
    return {'schemaVersion': 1, 'repository': 'https://github.com/' + repository,
            'commit': commit, 'path': path, 'blob': blob, 'content': content,
            'evidenceScope': 'repository-file-at-commit',
            'contentTrust': 'untrusted-upstream-evidence',
            'installationStarted': False, 'registered': False}


async def inspect_repository(url, library, transport=None, *, existing_roots=(), revision=None):
    requested = repository_identity(url)
    if revision is not None and (not isinstance(revision, str) or not re.fullmatch('[a-f0-9]{40}', revision)):
        raise ValueError('An immutable repository revision is required')
    async with httpx.AsyncClient(timeout=20, follow_redirects=False, transport=transport,
                                 headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'ODS-Extensions'}) as client:
        metadata = await _github_json(client, requested)
        if (not isinstance(metadata, dict) or metadata.get('private') is not False
                or not isinstance(metadata.get('full_name'), str)
                or metadata['full_name'].lower() != requested.lower()):
            raise ValueError('Repository identity could not be confirmed')
        branch = revision if revision is not None else metadata.get('default_branch')
        if not isinstance(branch, str) or not 1 <= len(branch) <= 255 or any(ord(c) < 32 for c in branch):
            raise ValueError('Repository branch is unavailable')
        commit = await _github_json(client, requested + '/commits/' + quote(branch, safe=''))
        sha = commit.get('sha') if isinstance(commit, dict) else None
        if not isinstance(sha, str) or not re.fullmatch(r'[a-f0-9]{40}', sha):
            raise ValueError('Repository revision could not be resolved')
        if revision is not None and sha != revision:
            raise ValueError('Repository revision does not match the proposal')
        readme = document(await _github_json(client, requested + '/readme?ref=' + sha, optional=True))
        license_response = await _github_json(client, requested + '/license?ref=' + sha, optional=True)
        license_file = document(license_response)
    # Repository metadata describes today's default branch, not necessarily
    # the immutable revision requested for this recipe.
    license_metadata = license_response.get('license') if isinstance(license_response, dict) else None
    spdx = license_metadata.get('spdx_id') if isinstance(license_metadata, dict) else None
    if not isinstance(spdx, str) or not re.fullmatch(r'[A-Za-z0-9.+-]{1,80}', spdx):
        spdx = None
    return {'schemaVersion': 1, 'repository': 'https://github.com/' + requested,
            'commit': sha, 'archived': metadata.get('archived') is True,
            'existingExtensionIds': existing_recipes(requested, *existing_roots, library),
            'licenseIdentifier': spdx, 'readme': readme, 'licenseText': license_file,
            'evidenceScope': 'repository-documents-at-commit',
            'contentTrust': 'untrusted-upstream-evidence',
            'installationStarted': False, 'registered': False,
            'requiresRecipeReview': True}


async def inspect_installation_layout(url, commit, transport=None):
    """Bounded source metadata for recipe design, never an inferred installer."""
    repository = repository_identity(url)
    if not isinstance(commit, str) or not re.fullmatch('[a-f0-9]{40}', commit):
        raise ValueError('An immutable revision is required')
    async with httpx.AsyncClient(timeout=10, follow_redirects=False, transport=transport,
            headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'ODS-Extensions'}) as client:
        entries = await _github_json(client, repository + '/contents?ref=' + commit)
        if not isinstance(entries, list) or len(entries) > 1000:
            raise ValueError('Repository layout is unavailable')
        files = sorted(item['name'] for item in entries if isinstance(item, dict)
            and item.get('type') == 'file' and isinstance(item.get('name'), str)
            and re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', item['name'])
            and item.get('path') == item['name'])
    # Only names actually returned by GitHub are fetched. Missing packaging
    # files are not fabricated or repeatedly probed by a small local model.
    priority = ('Dockerfile', 'compose.yaml', 'docker-compose.yml', 'pyproject.toml', 'setup.py',
                'package.json', 'Cargo.toml', 'go.mod', 'requirements.txt', 'Gemfile', 'pom.xml', 'build.gradle')
    documents = []
    remaining = 10000
    for name in [name for name in priority if name in files][:3]:
        evidence = await inspect_file(url, commit, name, transport=transport)
        content = evidence['content'][:min(5000, remaining)]
        remaining -= len(content)
        documents.append({'path': name, 'blob': evidence['blob'], 'content': content,
                          'truncated': len(content) < len(evidence['content'])})
        if remaining <= 0:
            break
    return {'repository': 'https://github.com/' + repository, 'commit': commit,
            'rootFiles': files[:128], 'rootFilesTruncated': len(files) > 128,
            'documents': documents, 'contentTrust': 'untrusted-upstream-evidence'}
