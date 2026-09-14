"""Runs inside the isolated Phoenix image; never prints credentials or trace content."""

from datetime import datetime, timedelta, timezone
import http.cookiejar
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest, ExportTraceServiceResponse,
)

BASE = "http://127.0.0.1:6006"
RECEIPT = Path("/data/phoenix/ods-test-receipt.json")
SPAN_ID = "0123456789abcdef"
OTLP_SPAN_ID = "1123456789abcdef"


def call(opener, path, method="GET", data=None, token=None, expected=(200,)):
    headers = {"Content-Type": "application/x-protobuf" if isinstance(data, bytes) else "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(BASE + path, method=method, headers=headers,
                                     data=data if isinstance(data, bytes) else json.dumps(data).encode() if data is not None else None)
    try:
        response = opener.open(request, timeout=20)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        raw = response.read()
        assert response.status in expected, f"{method} {path}: HTTP {response.status}"
        if isinstance(data, bytes):
            return raw
        return json.loads(raw) if raw else None


def login():
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    call(client, "/auth/login", "POST", {"email": "admin@localhost", "password": "invalid-test-password"}, expected=(401,))
    call(client, "/auth/login", "POST", {
        "email": "admin@localhost", "password": os.environ["PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD"],
    }, expected=(204,))
    return client


anonymous = urllib.request.build_opener(urllib.request.ProxyHandler({}))
call(anonymous, "/v1/projects", expected=(401, 403))
owner = login()

if sys.argv[1] == "write":
    key = call(owner, "/v1/system/api_keys", "POST", {"data": {"name": "ods-lifecycle"}}, expected=(201,))["data"]
    call(anonymous, "/v1/projects", "POST", {"name": "ods-lifecycle"}, token=key["key"], expected=(200, 201))
    now = datetime.now(timezone.utc)
    span = {
        "name": "generated-local-llm-test",
        "context": {"trace_id": "0123456789abcdef0123456789abcdef", "span_id": SPAN_ID},
        "span_kind": "LLM", "status_code": "OK",
        "start_time": now.isoformat(), "end_time": (now + timedelta(milliseconds=12)).isoformat(),
        "attributes": {"llm.model_name": "ods-fixture", "llm.token_count.prompt": 7, "llm.token_count.completion": 3},
    }
    receipt = call(anonymous, "/v1/projects/ods-lifecycle/spans", "POST", {"data": [span]},
                   token=key["key"], expected=(202,))
    assert receipt["total_queued"] == 1
    otlp = ExportTraceServiceRequest()
    resource = otlp.resource_spans.add()
    resource.resource.attributes.add(key="openinference.project.name").value.string_value = "ods-lifecycle"
    exported = resource.scope_spans.add().spans.add(
        name="generated-otlp-test", trace_id=bytes.fromhex("1123456789abcdef0123456789abcdef"),
        span_id=bytes.fromhex(OTLP_SPAN_ID), start_time_unix_nano=int(now.timestamp() * 1e9),
        end_time_unix_nano=int((now + timedelta(milliseconds=15)).timestamp() * 1e9),
    )
    exported.attributes.add(key="openinference.span.kind").value.string_value = "LLM"
    exported.status.code = 1
    call(anonymous, "/v1/traces", "POST", otlp.SerializeToString(), expected=(401, 403))
    result = ExportTraceServiceResponse.FromString(call(anonymous, "/v1/traces", "POST", otlp.SerializeToString(), token=key["key"]))
    assert result.partial_success.rejected_spans == 0
    RECEIPT.write_text(json.dumps({"key": key["key"], "key_id": key["id"]}))
    RECEIPT.chmod(0o600)
    print("Admin login, producer key creation and authenticated REST/OTLP span acceptance passed offline")
else:
    key = json.loads(RECEIPT.read_text())
    spans = call(anonymous, "/v1/projects/ods-lifecycle/spans", token=key["key"])["data"]
    span = next(value for value in spans if value["context"]["span_id"] == SPAN_ID)
    assert span["attributes"]["llm.token_count.prompt"] == 7
    assert span["attributes"]["llm.token_count.completion"] == 3
    assert any(value["context"]["span_id"] == OTLP_SPAN_ID for value in spans)
    call(owner, "/v1/system/api_keys/" + key["key_id"], "DELETE", expected=(200, 204))
    call(anonymous, "/v1/projects", token=key["key"], expected=(401, 403))
    RECEIPT.unlink()
    print("Account, producer key and REST/OTLP traces survived recreation offline; revoked key was rejected")
