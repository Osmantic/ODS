import sys
from pathlib import Path
import pytest
from copy import deepcopy

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'bin'))

from pixel_provider.connection import normalize_connection, normalize_probe, connection_url
from pixel_provider.store import StoreError

NOW = 1000

BASE_CONN = {
    "schemaVersion": 1,
    "kind": "ods-inference-connection",
    "label": "Laptop",
    "baseUrl": "http://127.0.0.1:4005/v1",
    "model": "ods/shared",
    "deviceId": "device-" + "a" * 16,
    "expiresAt": 2000,
    "expected": {"catalogId": "glm", "runtimeModelId": "GLM"},
    "credential": {"apiKey": "ods_infer_" + "b" * 64},
    "execution": "client-owned",
}

PROBE_ROOT = {
    "object": "list",
    "data": [{"id": "ods/shared", "object": "model"}],
    "ods": {
        "catalogId": "glm",
        "routedModel": "GLM",
        "identitySource": "ods-verified-route",
        "routeSeq": 4,
        "contextLength": 32768,
        "capabilities": {"chat": True, "tools": True, "vision": False, "agentViable": False},
        "maxOutputTokens": 4096,
        "expiresAt": 2000,
        "execution": "client-owned",
    },
}


def test_deep_copy():
    conn = deepcopy(BASE_CONN)
    result = normalize_connection(conn, now=NOW)
    assert conn is not result
    assert conn["baseUrl"] == result["baseUrl"]


def test_normalize_probe_success():
    probe = deepcopy(PROBE_ROOT)
    result = normalize_probe(probe, BASE_CONN, now=NOW)
    assert result["catalogId"] == "glm"
    assert result["routedModel"] == "GLM"


def test_connection_url():
    conn = normalize_connection(deepcopy(BASE_CONN), now=NOW)
    url = connection_url(conn['baseUrl'])
    assert url == "http://127.0.0.1:4005/v1"


@pytest.mark.parametrize("field, value", [
    ("schemaVersion", 2),
    ("kind", "wrong-kind"),
    ("label", 123),
    ("baseUrl", 123),
    ("model", 123),
    ("deviceId", 123),
    ("expiresAt", "string"),
    ("expected", "string"),
    ("credential", "string"),
    ("execution", "wrong-exec"),
])
def test_wrong_fields_types(field, value):
    conn = deepcopy(BASE_CONN)
    conn[field] = value
    with pytest.raises(StoreError) as exc_info:
        normalize_connection(conn, now=NOW)
    assert "invalid-connection" in str(exc_info.value)
    assert "secret" not in str(exc_info.value).lower()


@pytest.mark.parametrize("field, value", [
    ("chat", "true"),
    ("tools", 1),
    ("vision", "false"),
    ("agentViable", 0),
])
def test_bools_in_probe(field, value):
    probe = deepcopy(PROBE_ROOT)
    probe["ods"]["capabilities"][field] = value
    with pytest.raises(StoreError) as exc_info:
        normalize_probe(probe, BASE_CONN, now=NOW)
    assert "invalid-probe" in str(exc_info.value)


def test_revision_mismatch():
    probe = deepcopy(PROBE_ROOT)
    probe["ods"]["routeSeq"] = True
    with pytest.raises(StoreError) as exc_info:
        normalize_probe(probe, BASE_CONN, now=NOW)
    assert "invalid-probe" in str(exc_info.value)


def test_expiry_in_probe():
    probe = deepcopy(PROBE_ROOT)
    probe["ods"]["expiresAt"] = 999
    with pytest.raises(StoreError) as exc_info:
        normalize_probe(probe, BASE_CONN, now=NOW)
    assert "invalid-probe" in str(exc_info.value)


def test_unknown_secret():
    conn = deepcopy(BASE_CONN)
    conn["credential"] = {"unknownKey": "val"}
    with pytest.raises(StoreError) as exc_info:
        normalize_connection(conn, now=NOW)
    assert "invalid-connection" in str(exc_info.value)


def test_malformed_nested():
    conn = deepcopy(BASE_CONN)
    conn["expected"] = {"catalogId": 123}
    with pytest.raises(StoreError) as exc_info:
        normalize_connection(conn, now=NOW)
    assert "invalid-connection" in str(exc_info.value)


def test_identity_mismatch():
    probe = deepcopy(PROBE_ROOT)
    probe["ods"]["catalogId"] = "wrong"
    with pytest.raises(StoreError) as exc_info:
        normalize_probe(probe, BASE_CONN, now=NOW)
    assert "invalid-probe" in str(exc_info.value)


def test_caps_context_missing():
    probe = deepcopy(PROBE_ROOT)
    del probe["ods"]["capabilities"]
    with pytest.raises(StoreError) as exc_info:
        normalize_probe(probe, BASE_CONN, now=NOW)
    assert "invalid-probe" in str(exc_info.value)


def test_localhost_canonicalizes():
    conn = deepcopy(BASE_CONN)
    conn["baseUrl"] = "http://localhost:4005/v1"
    result = normalize_connection(conn, now=NOW)
    assert result["baseUrl"] == "http://127.0.0.1:4005/v1"


@pytest.mark.parametrize("url", [
    "http://user:pass@127.0.0.1:4005/v1",
    "http://127.0.0.1:4005/v1?query=1",
    "http://127.0.0.1:4005/v1#frag",
    "http://127.0.0.1:4005/v1/extra",
])
def test_url_rejected(url):
    conn = deepcopy(BASE_CONN)
    conn["baseUrl"] = url
    with pytest.raises(StoreError) as exc_info:
        normalize_connection(conn, now=NOW)
    assert "invalid-connection" in str(exc_info.value)


def test_tools_false_not_blocked():
    probe = deepcopy(PROBE_ROOT)
    probe["ods"]["capabilities"]["tools"] = False
    probe["ods"]["capabilities"]["agentViable"] = False
    result = normalize_probe(probe, BASE_CONN, now=NOW)
    assert result["capabilities"]["chat"] is True
