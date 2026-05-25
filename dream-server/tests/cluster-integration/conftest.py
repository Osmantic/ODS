import os
import time

import pytest
import requests


@pytest.fixture(scope="session")
def llama_url():
    return os.environ["LLAMA_URL"]


@pytest.fixture(scope="session", autouse=True)
def _wait_ready(llama_url):
    """Compose healthchecks already gate this, but double-check llama is up.

    We deliberately do NOT TCP-probe rpc1/rpc2 here: rpc-server accepts
    exactly one client connection, llama-server already holds both, and
    any extra connection attempt blocks until it times out. A healthy
    llama-server already proves both rpc workers were reachable AND
    handshook successfully (otherwise -ngl 99 model load would fail).
    """
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            r = requests.get(f"{llama_url}/health", timeout=3)
            if r.status_code == 200 and r.json().get("status") == "ok":
                return
        except requests.RequestException:
            pass
        time.sleep(1)
    raise AssertionError("llama-server /health never became ok")
