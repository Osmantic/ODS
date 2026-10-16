"""Ranged reads of a Hugging Face GGUF header (no network: MockTransport)."""

import asyncio

import httpx
import pytest

import hf_gguf_header
from gguf_inspector import inspect_gguf, parse_gguf_metadata
from test_gguf_inspector import STR, U32, build_gguf

REPO = "org/model-GGUF"
REVISION = "a" * 40
FILENAME = "model-Q4_K_M.gguf"
CDN = "https://us.aws.cdn.hf.co/xet-bridge-us/abc/def?Expires=1&Signature=x"
# conftest stubs fetch_gguf_header for every other test; this module tests the real one.
fetch_gguf_header = hf_gguf_header.fetch_gguf_header


@pytest.fixture(autouse=True)
def _empty_cache():
    hf_gguf_header._CACHE.clear()
    yield
    hf_gguf_header._CACHE.clear()


def _model_bytes(extra_keys=0) -> bytes:
    kvs = [
        ("general.architecture", STR, "qwen3"),
        ("general.file_type", U32, 15),
        ("qwen3.context_length", U32, 40960),
        ("qwen3.block_count", U32, 28),
        ("tokenizer.chat_template", STR, "{% if tools %}<tool_call>{% endif %}"),
    ]
    kvs += [(f"padding.key{index}", STR, "x" * 64) for index in range(extra_keys)]
    # Tensor data would follow the metadata; the reader must never need it.
    return build_gguf(kvs, tensor_count=3) + b"\0" * 4096


class _Hub:
    """A fake Hub + CDN that records every request."""

    def __init__(self, body: bytes, *, head_status=302, head_headers=None,
                 location=CDN, range_status=206):
        self.body = body
        self.head_status = head_status
        self.head_headers = head_headers or {}
        self.location = location
        self.range_status = range_status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "HEAD":
            headers = {"x-linked-size": str(len(self.body)), **self.head_headers}
            if self.head_status in {301, 302, 307}:
                headers["location"] = self.location
            return httpx.Response(self.head_status, headers=headers)
        if self.range_status != 206:
            return httpx.Response(self.range_status, content=self.body)
        start, end = request.headers["range"].removeprefix("bytes=").split("-")
        chunk = self.body[int(start):int(end) + 1]
        return httpx.Response(
            206, content=chunk,
            headers={"content-range": f"bytes {start}-{end}/{len(self.body)}"},
        )


def _fetch(hub: _Hub, *, expected_size=None, token=""):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(hub)) as client:
            return await fetch_gguf_header(
                REPO, REVISION, FILENAME,
                expected_size=expected_size, token=token, client=client,
            )
    return asyncio.run(run())


def test_reads_only_the_metadata_block_and_parses_it(monkeypatch):
    body = _model_bytes()
    hub = _Hub(body)

    result = _fetch(hub, expected_size=len(body))

    assert result["architecture"] == "qwen3"
    assert result["context_length"] == 40960
    assert result["metadata"]["tokenizer.chat_template"].startswith("{% if tools %}")
    assert result["bytes_read"] <= len(body)
    head, first_range = hub.requests[0], hub.requests[1]
    assert head.method == "HEAD"
    assert str(head.url) == f"https://huggingface.co/{REPO}/resolve/{REVISION}/{FILENAME}"
    assert first_range.headers["range"].startswith("bytes=0-")


def test_parse_matches_the_local_inspector(tmp_path):
    body = _model_bytes()
    path = tmp_path / "m.gguf"
    path.write_bytes(body)

    local = inspect_gguf(path)
    parsed = parse_gguf_metadata(body)

    for key, value in parsed.items():
        assert local[key] == value


def test_grows_the_range_until_the_metadata_ends(monkeypatch):
    monkeypatch.setattr(hf_gguf_header, "FIRST_READ_BYTES", 64)
    body = _model_bytes(extra_keys=40)
    hub = _Hub(body)

    result = _fetch(hub, expected_size=len(body))

    ranges = [request.headers["range"] for request in hub.requests if request.method == "GET"]
    assert ranges[0] == "bytes=0-63"
    assert ranges[1] == "bytes=64-127"
    # Each request continues where the previous one stopped.
    starts = [int(value.removeprefix("bytes=").split("-")[0]) for value in ranges]
    assert starts == sorted(starts) and len(set(starts)) == len(starts)
    assert result["metadata"]["padding.key39"] == "x" * 64


def test_header_larger_than_the_cap_is_refused(monkeypatch):
    monkeypatch.setattr(hf_gguf_header, "FIRST_READ_BYTES", 64)
    monkeypatch.setattr(hf_gguf_header, "MAX_READ_BYTES", 256)
    body = _model_bytes(extra_keys=40)
    hub = _Hub(body)

    with pytest.raises(hf_gguf_header.HeaderUnavailable) as raised:
        _fetch(hub, expected_size=len(body))

    assert raised.value.code == "header_too_large"


def test_server_that_ignores_range_is_refused_without_reading_the_body():
    hub = _Hub(_model_bytes(), range_status=200)

    with pytest.raises(hf_gguf_header.HeaderUnavailable) as raised:
        _fetch(hub)

    assert raised.value.code == "range_unsupported"


@pytest.mark.parametrize("location", [
    "http://us.aws.cdn.hf.co/file",
    "https://evil.example/file",
    "https://hf.co.evil.example/file",
])
def test_redirect_off_hugging_face_is_refused(location):
    hub = _Hub(_model_bytes(), location=location)

    with pytest.raises(hf_gguf_header.HeaderUnavailable) as raised:
        _fetch(hub)

    assert raised.value.code == "redirect_refused"
    assert all(request.method == "HEAD" for request in hub.requests)


@pytest.mark.parametrize("status,headers,code", [
    (401, {"x-error-code": "GatedRepo"}, "gated"),
    (403, {"x-error-code": "GatedRepo"}, "gated"),
    (401, {}, "unavailable"),
    (404, {}, "not_found"),
    (429, {}, "rate_limited"),
    (500, {}, "upstream_error"),
])
def test_hub_refusals_map_to_reasons(status, headers, code):
    hub = _Hub(_model_bytes(), head_status=status, head_headers=headers)

    with pytest.raises(hf_gguf_header.HeaderUnavailable) as raised:
        _fetch(hub)

    assert raised.value.code == code


def test_size_mismatch_is_reported():
    body = _model_bytes()
    hub = _Hub(body)

    with pytest.raises(hf_gguf_header.HeaderUnavailable) as raised:
        _fetch(hub, expected_size=len(body) + 1)

    assert raised.value.code == "size_mismatch"


def test_token_goes_to_the_hub_but_never_to_the_cdn():
    hub = _Hub(_model_bytes())

    _fetch(hub, token="hf_test_token_value")

    head = hub.requests[0]
    assert head.headers["authorization"] == "Bearer hf_test_token_value"
    assert all("authorization" not in request.headers for request in hub.requests[1:])


def test_not_a_gguf_file():
    hub = _Hub(b"PK\x03\x04" + b"\0" * 512)

    with pytest.raises(hf_gguf_header.HeaderUnavailable) as raised:
        _fetch(hub)

    assert raised.value.code == "not_gguf"


def test_file_that_ends_inside_its_metadata_is_unreadable():
    body = _model_bytes()[:40]
    hub = _Hub(body)

    with pytest.raises(hf_gguf_header.HeaderUnavailable) as raised:
        _fetch(hub, expected_size=len(body))

    assert raised.value.code == "unreadable"


def test_second_read_of_the_same_file_uses_the_cache():
    hub = _Hub(_model_bytes())

    first = _fetch(hub)
    calls = len(hub.requests)
    second = _fetch(hub)

    assert second == first
    assert len(hub.requests) == calls


def test_network_failure_is_unreachable():
    def fail(_request):
        raise httpx.ConnectError("no route")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
            return await fetch_gguf_header(REPO, REVISION, FILENAME, client=client)

    with pytest.raises(hf_gguf_header.HeaderUnavailable) as raised:
        asyncio.run(run())

    assert raised.value.code == "unreachable"


@pytest.mark.parametrize("url,allowed", [
    ("https://us.aws.cdn.hf.co/x", True),
    ("https://cas-bridge.xethub.hf.co/x", True),
    ("https://huggingface.co/api/resolve-cache/x", True),
    ("https://hf.co/x", True),
    ("http://huggingface.co/x", False),
    ("https://huggingface.co.evil.example/x", False),
    ("https://evilhf.co/x", False),
])
def test_redirect_allowlist(url, allowed):
    assert hf_gguf_header.redirect_allowed(url) is allowed


def test_cached_header_is_returned_without_a_request():
    hub = _Hub(_model_bytes())

    assert hf_gguf_header.cached_gguf_header(REPO, REVISION, FILENAME) is None
    first = _fetch(hub)
    calls = len(hub.requests)

    assert hf_gguf_header.cached_gguf_header(REPO, REVISION, FILENAME) == first
    assert len(hub.requests) == calls


def _model_with_tensor_table(types=(12, 14, 0)) -> bytes:
    from test_gguf_inspector import tensor_info

    kvs = [("general.architecture", STR, "qwen3"), ("general.file_type", U32, 15)]
    kvs += [(f"padding.key{index}", STR, "x" * 64) for index in range(20)]
    table = b"".join(tensor_info(f"blk.{index}.weight", (1024, 1024), ggml_type)
                     for index, ggml_type in enumerate(types))
    return build_gguf(kvs, tensor_count=len(types)) + table + b"\1" * 8192


def test_reads_on_for_the_tensor_table_after_the_metadata(monkeypatch):
    body = _model_with_tensor_table()
    metadata_end = parse_gguf_metadata(body)["metadata_bytes"]
    # The first read ends just past the metadata, inside the tensor table.
    monkeypatch.setattr(hf_gguf_header, "FIRST_READ_BYTES", metadata_end + 8)
    hub = _Hub(body)

    result = _fetch(hub, expected_size=len(body))

    assert result["tensor_types"] == [0, 12, 14]
    ranges = [request.headers["range"] for request in hub.requests if request.method == "GET"]
    assert len(ranges) == 2


def test_a_failed_read_past_the_metadata_keeps_the_metadata(monkeypatch):
    body = _model_with_tensor_table()
    metadata_end = parse_gguf_metadata(body)["metadata_bytes"]
    monkeypatch.setattr(hf_gguf_header, "FIRST_READ_BYTES", metadata_end + 8)
    hub = _Hub(body)
    served: list[httpx.Request] = []

    def flaky(request):
        if request.method == "GET" and served:
            return httpx.Response(503)
        if request.method == "GET":
            served.append(request)
        return hub(request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(flaky)) as client:
            # The real reader (conftest stubs the module attribute for every other test).
            return await fetch_gguf_header(
                REPO, REVISION, FILENAME, expected_size=len(body), token="", client=client)

    result = asyncio.run(run())

    assert result["architecture"] == "qwen3"
    assert result["tensor_types"] is None
