"""Actual in-container HTTP probe; only synthetic validation data is used."""

import base64
import json
import os
import urllib.error
import urllib.request


def request(password=None):
    headers = {"Content-Type": "application/json"}
    if password is not None:
        auth = base64.b64encode(("ods:" + password).encode()).decode()
        headers["Authorization"] = "Basic " + auth
    req = urllib.request.Request("http://127.0.0.1:8080/api/home/workspace_files",
                                 data=b"{}", headers=headers)
    try:
        response = urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return response.status, response.read(), response.headers


for invalid in (None, os.environ.get("ODS_OLD_MARIMO_PASSWORD", "incorrect-password")):
    status, body, _ = request(invalid)
    assert status in (401, 403), (status, body)
    assert b"analysis.py" not in body
status, body, headers = request(os.environ["MARIMO_PASSWORD"])
assert status == 200, (status, body)
payload = json.loads(body)
assert payload["root"] == "/workspace", payload
assert any(file["name"] == "analysis.py" for file in payload["files"]), payload
cookie = headers.get("Set-Cookie", "")
assert cookie
session = cookie.split("session=", 1)[1].split(".", 1)[0]
decoded = base64.b64decode(session + "=" * (-len(session) % 4)).decode()
assert os.environ["MARIMO_PASSWORD"] not in decoded
print("HTTP rejects missing/wrong credentials; authenticated workspace listing and non-secret session cookie passed")
