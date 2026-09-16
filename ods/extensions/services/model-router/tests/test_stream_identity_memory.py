"""Stream identity validation should retain a verdict, not every model label."""
import json
import tracemalloc
import uuid

import pytest
from test_router import _set_stream_upstream, _signed_marker
from test_router import router as router  # noqa: PLC0414


def chunk(model, *, tail=False):
    payload = {'model': model, 'choices': [{'delta': {'content': 'x'}}]}
    return b'data: ' + json.dumps(payload).encode() + (b'' if tail else b'\n\n')


def test_model_identity_storage_does_not_grow_with_token_events(router):
    mod, _, _, _ = router
    rewriter = mod._SSERewriter('ods/current', 'Concrete.gguf')
    event = chunk('Concrete.gguf')
    tracemalloc.start()
    try:
        for _ in range(1000):
            rewriter.feed(event)
        before = tracemalloc.get_traced_memory()[0]
        for _ in range(5000):
            rewriter.feed(event)
        growth = tracemalloc.get_traced_memory()[0] - before
        assert growth < 128 * 1024
        assert rewriter.identity_matches
    finally:
        tracemalloc.stop()


@pytest.mark.parametrize('tail', [False, True])
@pytest.mark.parametrize('pinned', [False, True])
def test_late_identity_change_is_not_forgotten(router, tail, pinned):
    mod, client, write_state, _ = router
    write_state()
    probe = str(uuid.uuid4())
    chunks = [chunk('Concrete.gguf')] * 300
    chunks += [chunk('Wrong.gguf', tail=tail)]
    if not tail:
        chunks += [chunk('Concrete.gguf'), b'data: [DONE]\n\n']
    _set_stream_upstream(mod, chunks)
    headers = {
        'X-ODS-Expected-Catalog': 'concrete',
        'X-ODS-Expected-Model': 'Concrete.gguf',
        'X-ODS-Expected-Route': '7',
    } if pinned else {}
    request = {'model': 'ods/current', 'stream': True, 'messages': [{'role': 'user', 'content': _signed_marker(probe)}]}
    if pinned:
        with pytest.raises(mod.RouterError, match='Backend response identity changed'):
            client.post('/v1/chat/completions', json=request, headers=headers)
    else:
        response = client.post('/v1/chat/completions', json=request)
        assert response.status_code == 200
        assert 'Wrong.gguf' not in response.text
        assert 'ods/current' in response.text
    evidence = client.get(f'/internal/route-evidence/{probe}', headers={'Authorization': 'Bearer internal-secret'})
    assert evidence.status_code == 404
    assert mod._inflight == 0
