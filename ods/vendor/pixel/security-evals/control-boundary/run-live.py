#!/usr/bin/env python3
"""Exercise the deployed loopback control API without disclosing its credential."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.request


MAX_RESPONSE_BYTES = 64 * 1024


def fail(message: str) -> None:
    raise SystemExit(f"control-boundary failed: {message}")


def exact_ignoring_whitespace(value: object, expected: str) -> bool:
    """Permit model-inserted formatting whitespace, but no other extra content."""
    return isinstance(value, str) and "".join(value.split()) == expected


def post(url: str, authorization: str | None, body: dict[str, object]) -> tuple[int, bytes]:
    headers = {"Content-Type": "application/json"}
    if authorization is not None:
        headers["Authorization"] = f"Bearer {authorization}"
    request = urllib.request.Request(
        url,
        data=json.dumps(body, separators=(",", ":")).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        return error.code, error.read(MAX_RESPONSE_BYTES + 1)
    except (OSError, urllib.error.URLError) as error:
        fail(f"gateway request failed ({type(error).__name__})")


def main() -> int:
    home = Path(os.environ.get("OPENCLAW_STATE_DIR", Path.home() / ".openclaw"))
    config_path = Path(os.environ.get("OPENCLAW_CONFIG_PATH", home / "openclaw.json"))
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        token = config["gateway"]["auth"]["token"]
        agent = os.environ.get("PIXEL_AGENT_ID") or config["agents"]["list"][0]["id"]
        port = int(os.environ.get("PIXEL_GATEWAY_PORT", "18789"))
    except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError):
        fail("cannot load the deployed gateway identity")
    if not isinstance(token, str) or len(token) < 32:
        fail("gateway credential is missing or malformed")
    if not isinstance(agent, str) or not agent:
        fail("agent ID is missing")

    url = f"http://127.0.0.1:{port}/v1/chat/completions"
    probe = {
        "model": f"openclaw/{agent}",
        "messages": [{"role": "user", "content": "Return exactly NO."}],
        "stream": False,
    }
    unauthenticated, unauthenticated_body = post(url, None, probe)
    forged, forged_body = post(url, "pixel-invalid-token-probe", probe)
    for label, status, body in (
        ("unauthenticated", unauthenticated, unauthenticated_body),
        ("forged", forged, forged_body),
    ):
        if status not in (401, 404):
            fail(f"{label} request reached the agent (HTTP {status})")
        if token.encode() in body:
            fail(f"{label} response disclosed the gateway credential")

    expected = "PIXEL_CONTROL_API_OK"
    valid, valid_body = post(
        url,
        token,
        {
            "model": f"openclaw/{agent}",
            "messages": [{
                "role": "user",
                "content": f"Do not use tools. Return exactly {expected} and nothing else.",
            }],
            "stream": False,
        },
    )
    if len(valid_body) > MAX_RESPONSE_BYTES:
        fail("authenticated response exceeded the bounded evidence size")
    if valid != 200:
        fail(f"authenticated request failed (HTTP {valid})")
    try:
        content = json.loads(valid_body)["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        fail("authenticated response did not match the expected API schema")
    if not exact_ignoring_whitespace(content, expected):
        fail("authenticated safe-capability result was not exact")
    if token.encode() in valid_body:
        fail("authenticated response disclosed the gateway credential")

    unit = os.environ.get("PIXEL_SYSTEMD_UNIT", "openclaw-gateway.service")
    journal = subprocess.run(
        ["journalctl", "-u", unit, "--since", "-15 minutes", "--no-pager", "-o", "cat"],
        check=False,
        capture_output=True,
    )
    if journal.returncode:
        fail("cannot inspect gateway service logs")
    if token.encode() in journal.stdout or token.encode() in journal.stderr:
        fail("gateway credential appeared in service logs")

    print(json.dumps({
        "authenticatedSafeCapability": "pass",
        "credentialInLogs": False,
        "credentialInResponses": False,
        "forgedStatus": forged,
        "status": "pass",
        "unauthenticatedStatus": unauthenticated,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
