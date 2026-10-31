"""The real app in the Windows Desktop profile serves the dashboard behind its gate.

Imported in a fresh interpreter, as the profile is read at import.
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parent.parent

_PROBE = textwrap.dedent(
    """
    import asyncio, json, sys
    sys.path.insert(0, sys.argv[1])
    import httpx
    import main

    async def request(path, client, method="GET", headers=None):
        transport = httpx.ASGITransport(app=main.app, client=client)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:3101") as http:
            response = await http.request(method, path, headers=headers)
            is_json = response.headers.get("content-type", "").startswith("application/json")
            return {"status": response.status_code, "signIn": response.headers.get("x-ods-sign-in"),
                    "profile": response.json().get("runtimeProfile") if is_json else None,
                    "body": response.text[:200]}

    async def run():
        local, network = ("127.0.0.1", 50123), ("192.168.1.20", 50123)
        return {
            "index": await request("/", local),
            "client_route": await request("/settings", local),
            "status_local": await request("/api/status", local),
            "status_network": await request("/api/status", network),
            "status_cross_site": await request("/api/status", local, headers={"Sec-Fetch-Site": "cross-site"}),
            "unknown_api": await request("/api/does-not-exist", local),
        }

    print(json.dumps(asyncio.run(run())))
    """
)


def test_desktop_profile_serves_the_dashboard_behind_its_gate(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>ODS dashboard</html>", encoding="utf-8")
    env = {
        **os.environ,
        "ODS_RUNTIME_PROFILE": "windows-desktop",
        "ODS_DATA_DIR": str(tmp_path / "data"),
        "ODS_INSTALL_DIR": str(tmp_path / "install"),
        "ODS_EXTENSIONS_DIR": str(API_DIR.parent),
        "DASHBOARD_API_KEY": "desktop-probe-key",
        # #7975 checks the local model settings at startup.
        "ODS_NATIVE_LLM_URL": "http://127.0.0.1:18080",
        "ODS_NATIVE_LLM_RUNTIME_DIR": str(tmp_path / "portal-runtime"),
        "ODS_DASHBOARD_DIST": str(dist),
    }
    env.pop("GPU_BACKEND", None)
    result = subprocess.run([sys.executable, "-c", _PROBE, str(API_DIR)],
                            capture_output=True, text=True, env=env, timeout=120)
    assert result.returncode == 0, result.stderr[-4000:]
    probe = json.loads(result.stdout.strip().splitlines()[-1])

    assert probe["index"]["status"] == 200 and "ODS dashboard" in probe["index"]["body"]
    assert probe["client_route"]["status"] == 200 and "ODS dashboard" in probe["client_route"]["body"]
    # The local browser sends no key; the gate adds the server-held one.
    assert probe["status_local"]["status"] == 200 and probe["status_local"]["profile"] == "windows-desktop"
    for refused in ("status_network", "status_cross_site"):
        assert probe[refused]["status"] == 401 and probe[refused]["signIn"] == "required"
    # An unknown API path is an API 404, never the dashboard's index.html.
    assert probe["unknown_api"]["status"] == 404 and "ODS dashboard" not in probe["unknown_api"]["body"]

# `python main.py` with uvicorn.run caught, so no listener opens.
_ENTRYPOINT_PROBE = textwrap.dedent(
    """
    import json, runpy, sys
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    import uvicorn
    calls = []
    uvicorn.run = lambda app, **options: calls.append(options)
    runpy.run_path(str(Path(sys.argv[1]) / "main.py"), run_name="__main__")
    print(json.dumps(calls))
    """
)


@pytest.mark.parametrize("profile, host", [("windows-desktop", "127.0.0.1"), ("compose", "0.0.0.0")])
def test_direct_entrypoint_binds_the_profile_host(tmp_path, profile, host):
    env = {
        **os.environ,
        "ODS_RUNTIME_PROFILE": profile,
        "ODS_DATA_DIR": str(tmp_path / "data"),
        "ODS_INSTALL_DIR": str(tmp_path / "install"),
        "ODS_EXTENSIONS_DIR": str(API_DIR.parent),
        "DASHBOARD_API_KEY": "entrypoint-probe-key",
        "DASHBOARD_API_PORT": "3101",
        "ODS_NATIVE_LLM_URL": "http://127.0.0.1:18080",
        "ODS_NATIVE_LLM_RUNTIME_DIR": str(tmp_path / "portal-runtime"),
    }
    env.pop("GPU_BACKEND", None)
    result = subprocess.run([sys.executable, "-c", _ENTRYPOINT_PROBE, str(API_DIR)],
                            capture_output=True, text=True, env=env, timeout=120)
    assert result.returncode == 0, result.stderr[-4000:]
    calls = json.loads(result.stdout.strip().splitlines()[-1])
    assert calls == [{"host": host, "port": 3101}]
