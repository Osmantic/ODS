#!/usr/bin/env python3
"""Read-only, redacted diagnostics for a disposable Portal acceptance runner."""

import http.client
import json
import re
import sys
from pathlib import Path


def env_values(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, raw = line.strip().partition("=")
        if separator and re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            value = raw.strip()
            if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[key] = value
    return values


def request(port: int, key: str, path: str) -> dict:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
    try:
        connection.request("GET", path, headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
        response = connection.getresponse()
        status = response.status
        content_type = response.headers.get_content_type()
        payload = response.read(131072)
    except (OSError, TimeoutError, http.client.HTTPException) as error:
        return {"transportErrorType": type(error).__name__}
    finally:
        connection.close()
    result = {"httpStatus": status, "contentType": content_type}
    try:
        value = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        result["json"] = False
        return result
    if not isinstance(value, dict):
        result["jsonObject"] = False
        return result
    result["jsonKeys"] = sorted(value)[:32]
    for field in ("schemaVersion", "id", "status", "state", "extensionId"):
        if isinstance(value.get(field), (str, int)):
            result[field] = value[field]
    if isinstance(value.get("env_vars"), list):
        result["envVarCount"] = len(value["env_vars"])
    if isinstance(value.get("steps"), list):
        result["planStepCount"] = len(value["steps"])
    if isinstance(value.get("integration"), dict):
        result["integrationKeys"] = sorted(value["integration"])[:32]
    return result


def main() -> int:
    values = env_values(Path(sys.argv[1]))
    extension_id = sys.argv[2]
    port_text = values.get("DASHBOARD_API_PORT", "3002")
    key = values.get("DASHBOARD_API_KEY", "")
    result = {"portValid": port_text.isdecimal() and 1 <= int(port_text) <= 65535,
              "credentialShapeValid": bool(re.fullmatch(r"[a-fA-F0-9]{64}", key))}
    if result["portValid"] and result["credentialShapeValid"]:
        port = int(port_text)
        result["health"] = request(port, key, "/health")
        result["detail"] = request(port, key, f"/api/extensions/{extension_id}")
        result["plan"] = request(port, key, f"/api/extensions/{extension_id}/install-plan")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
