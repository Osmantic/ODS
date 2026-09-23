"""GitHub cooldowns must stay distinguishable from invalid owner input."""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from routers import extensions


@pytest.mark.parametrize('endpoint,lookup,payload', [
    ('extension_github_inspect', 'inspect_repository', {'url': 'https://github.com/owner/repo'}),
    ('extension_github_file', 'inspect_file', {
        'url': 'https://github.com/owner/repo', 'commit': 'a' * 40, 'path': 'README.md'}),
])
def test_repository_rate_limit_returns_bounded_retry_not_invalid_input(monkeypatch, endpoint, lookup, payload):
    from extension_github import GitHubRateLimitError

    monkeypatch.setattr('extension_github.' + lookup, AsyncMock(side_effect=GitHubRateLimitError(120)))

    async def receive():
        return {'type': 'http.request', 'body': json.dumps(payload).encode(), 'more_body': False}

    request = Request({'type': 'http', 'method': 'POST', 'headers': []}, receive)
    with pytest.raises(extensions.HTTPException) as rejected:
        asyncio.run(getattr(extensions, endpoint)(request, api_key='owner'))
    assert rejected.value.status_code == 429
    assert rejected.value.detail == {'code': 'github-rate-limited', 'retryAfter': 120}
    assert rejected.value.headers == {'Retry-After': '120'}
