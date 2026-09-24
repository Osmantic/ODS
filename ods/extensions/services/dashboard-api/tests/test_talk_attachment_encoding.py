from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def attachment_client(test_client, monkeypatch):
    import routers.talk as talk
    import session_signer

    monkeypatch.setenv('ODS_SESSION_SECRET', 'attachment-encoding-test-secret')
    session_signer._set_secret_for_tests('attachment-encoding-test-secret')
    test_client.cookies.set('ods-session', session_signer.issue(ttl_seconds=3600))
    monkeypatch.setattr(talk, '_require_hermes_talk_compatible', AsyncMock())
    prompts = []

    async def stream(_session, text, _request):
        prompts.append(text)
        yield b'data: {"type":"done"}\n\n'

    monkeypatch.setattr(talk, '_stream_hermes_sse', stream)
    return test_client, prompts


@pytest.mark.parametrize('encoding', ['utf-8-sig', 'utf-16', 'utf-16-be'])
def test_text_attachment_preserves_bom_marked_unicode(attachment_client, encoding):
    client, prompts = attachment_client
    text = 'Original text: \u65e5\u672c\u8a9e \U0001f680'
    data = text.encode(encoding)
    if encoding == 'utf-16-be':
        data = b'\xfe\xff' + data
    response = client.post('/api/talk/attachment', files={'file': ('notes.txt', data, 'text/plain')})
    assert response.status_code == 200, response.text
    assert text in prompts[0]
    assert '\ufffd' not in prompts[0]
    assert '\ufeff' not in prompts[0]


@pytest.mark.parametrize('data', [b'\xffbroken', b'hello\x00world'])
def test_binary_or_invalid_text_never_reaches_inference(attachment_client, data):
    client, prompts = attachment_client
    response = client.post('/api/talk/attachment', files={'file': ('notes.txt', data, 'text/plain')})
    assert response.status_code == 422, response.text
    assert not prompts


def test_parameterized_text_mime_without_extension(attachment_client):
    client, prompts = attachment_client
    response = client.post('/api/talk/attachment', files={'file': ('notes', b'exact content', 'text/plain; charset=utf-8')})
    assert response.status_code == 200, response.text
    assert 'exact content' in prompts[0]
