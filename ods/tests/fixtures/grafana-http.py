"""Actual Grafana API workflow against isolated synthetic metrics and dashboards."""

import base64
import json
import os
import urllib.error
import urllib.request

password = os.environ["ODS_GRAFANA_LOGIN_PASSWORD"]


def request(path, method="GET", data=None, credential=password):
    headers = {"Content-Type": "application/json"}
    if credential is not None:
        headers["Authorization"] = "Basic " + base64.b64encode(("admin:" + credential).encode()).decode()
    req = urllib.request.Request("http://127.0.0.1:3000" + path, method=method,
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers=headers)
    try:
        response = urllib.request.urlopen(req, timeout=20)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return response.status, response.read()


status, _ = request("/api/search", credential=None)
assert status == 401, status
status, body = request("/api/user")
assert status == 200, (status, body)
assert json.loads(body)["login"] == "admin"
if os.environ["ODS_GRAFANA_SEED"] == "1":
    status, body = request("/api/datasources", "POST", {
        "name": "ODS synthetic metrics", "uid": "ods-fixture", "type": "prometheus",
        "access": "proxy", "url": "http://127.0.0.1:18080", "basicAuth": True,
        "basicAuthUser": "ods", "secureJsonData": {"basicAuthPassword": os.environ["ODS_METRICS_PASSWORD"]},
        "jsonData": {"httpMethod": "POST", "prometheusType": "Prometheus", "prometheusVersion": "2.54.1"},
    })
    assert status in (200, 201), (status, body)
    assert os.environ["ODS_METRICS_PASSWORD"].encode() not in body
    status, body = request("/api/dashboards/db", "POST", {"dashboard": {
        "id": None, "uid": "ods-local-proof", "title": "ODS local metrics proof", "schemaVersion": 41,
        "panels": [{"id": 1, "type": "timeseries", "title": "Synthetic signal",
                    "gridPos": {"h": 8, "w": 12, "x": 0, "y": 0},
                    "datasource": {"type": "prometheus", "uid": "ods-fixture"},
                    "targets": [{"refId": "A", "expr": "ods_fixture", "range": True}]}],
    }, "overwrite": False})
    assert status == 200, (status, body)
status, body = request("/api/dashboards/uid/ods-local-proof")
assert status == 200, (status, body)
assert json.loads(body)["dashboard"]["panels"][0]["targets"][0]["expr"] == "ods_fixture"
status, body = request("/api/datasources/uid/ods-fixture")
assert status == 200, (status, body)
assert json.loads(body)["secureJsonFields"]["basicAuthPassword"] is True
assert os.environ["ODS_METRICS_PASSWORD"].encode() not in body
status, body = request("/api/ds/query", "POST", {"from": "1740000000000", "to": "1740000060000", "queries": [{
    "refId": "A", "datasource": {"uid": "ods-fixture", "type": "prometheus"},
    "expr": "ods_fixture", "range": True, "instant": False, "intervalMs": 60000, "maxDataPoints": 10,
}]})
assert status == 200, (status, body)
payload = json.loads(body)
assert not payload["results"]["A"].get("error"), payload
frames = payload["results"]["A"]["frames"]
assert any(3.14 in values for frame in frames for values in frame["data"]["values"]), payload
with urllib.request.urlopen("http://127.0.0.1:18080/seen", timeout=5) as response:
    assert any("query_range" in path for path in json.load(response))
if os.environ["ODS_GRAFANA_SEED"] != "1":
    status, _ = request("/api/user", credential=os.environ["ODS_GRAFANA_RESEED_PASSWORD"])
    assert status == 401, "Changing the seed environment must not silently reset the established account"
print("Real Grafana authentication, saved dashboard, encrypted source credentials and metrics query passed")
