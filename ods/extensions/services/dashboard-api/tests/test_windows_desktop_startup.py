"""The Desktop profile starts no child process and never dials a host agent.

A windowless Desktop process flashes a console for every child it starts, and
any host-agent address could reach a Docker or WSL ODS on the same PC. The app
is imported in a fresh interpreter because the profile is read at import.
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent

_PROBE = textwrap.dedent(
    """
    import asyncio, json, subprocess, sys, time

    started = []

    class RefusedPopen(subprocess.Popen):
        def __init__(self, args, *rest, **kwargs):
            started.append(args if isinstance(args, str) else list(map(str, args)))
            raise OSError("child processes are refused in this probe")

    async def refused_exec(program, *args, **kwargs):
        started.append([str(program), *map(str, args)])
        raise OSError("child processes are refused in this probe")

    subprocess.Popen = RefusedPopen
    asyncio.create_subprocess_exec = refused_exec

    sys.path.insert(0, sys.argv[1])
    from fastapi.testclient import TestClient
    import config, host_agent_client, main
    from runtime_profile import is_loopback_host

    headers = {"Authorization": "Bearer desktop-probe-key"}
    with TestClient(main.app) as client:
        time.sleep(float(sys.argv[2]))  # at least one 5 s poll of every loop
        status = client.get("/api/status", headers=headers)
        docker = client.get("/api/preflight/docker", headers=headers)
        try:
            host_agent_client.request_json("GET", "/health")
            agent = "reached"
        except host_agent_client.AgentUnavailable as exc:
            agent = str(exc)

    print(json.dumps({
        "started": started,
        "agent_url": config.AGENT_URL,
        "agent": agent,
        "sync_client": host_agent_client._sync_client is not None,
        "async_client": host_agent_client._async_client is not None,
        "non_loopback_services": sorted(
            sid for sid, svc in config.SERVICES.items() if not is_loopback_host(svc["host"])),
        "status_code": status.status_code,
        "status": status.json(),
        "docker": docker.json(),
    }))
    """
)


def _run_desktop_probe(tmp_path, seconds="6"):
    env = {
        **os.environ,
        "ODS_RUNTIME_PROFILE": "windows-desktop",
        "ODS_DATA_DIR": str(tmp_path / "data"),
        "ODS_INSTALL_DIR": str(tmp_path / "install"),
        # The real bundled manifests: every Compose service is declared here.
        "ODS_EXTENSIONS_DIR": str(API_DIR.parent),
        "DASHBOARD_API_KEY": "desktop-probe-key",
        # A reachable-looking agent address must still never be dialed.
        "ODS_AGENT_HOST": "127.0.0.1",
    }
    env.pop("GPU_BACKEND", None)
    result = subprocess.run(
        [sys.executable, "-c", _PROBE, str(API_DIR), seconds],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert result.returncode == 0, result.stderr[-4000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_desktop_profile_runs_without_child_processes_or_host_agent(tmp_path):
    probe = _run_desktop_probe(tmp_path)

    assert probe["started"] == []
    assert probe["agent_url"] == ""
    assert "has no host agent" in probe["agent"]
    assert not probe["sync_client"] and not probe["async_client"]

    # Compose service names resolve to nothing on a native PC, and every
    # failed lookup stalls a status probe for seconds on Windows.
    assert probe["non_loopback_services"] == []
    probed = {service["id"] for service in probe["status"]["services"]}
    assert not probed & {"llama-server", "litellm", "open-webui", "ape", "dashboard-api"}

    assert probe["status_code"] == 200
    assert probe["status"]["runtimeProfile"] == "windows-desktop"
    assert probe["docker"] == {
        "available": False, "applicable": False,
        "error": "The Windows Desktop profile does not use Docker",
    }


def test_desktop_profile_refuses_to_start_without_its_data_dir(tmp_path):
    env = {**os.environ, "ODS_RUNTIME_PROFILE": "windows-desktop",
           "DASHBOARD_API_KEY": "desktop-probe-key"}
    env.pop("ODS_DATA_DIR", None)
    result = subprocess.run(
        [sys.executable, "-c", f"import sys; sys.path.insert(0, {str(API_DIR)!r}); import main"],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert result.returncode != 0
    assert "RuntimeProfileError" in result.stderr and "ODS_DATA_DIR" in result.stderr
