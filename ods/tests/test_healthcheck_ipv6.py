"""Universal healthcheck script must support bracketed IPv6 TCP targets."""

import json
import socket
import subprocess
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEALTHCHECK = ROOT / "scripts/healthcheck.py"


def test_healthcheck_parses_and_connects_ipv6():
    # Start a background IPv6 TCP listener
    server = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    try:
        server.bind(("::1", 0))
    except OSError:
        # Host environment has no IPv6 loopback configured
        return
    server.listen(1)
    port = server.getsockname()[1]

    def _accept():
        try:
            conn, _ = server.accept()
            conn.close()
        except OSError:
            pass

    t = threading.Thread(target=_accept, daemon=True)
    t.start()

    # 1. Test bracketed shorthand: [::1]:port
    res = subprocess.run(
        ["python3", str(HEALTHCHECK), f"[::1]:{port}", "--timeout", "2", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"stdout: {res.stdout}, stderr: {res.stderr}"
    data = json.loads(res.stdout)
    assert data["ok"] is True
    assert data["kind"] == "tcp"

    # Start listener for second request
    t2 = threading.Thread(target=_accept, daemon=True)
    t2.start()

    # 2. Test tcp://[::1]:port format
    res2 = subprocess.run(
        ["python3", str(HEALTHCHECK), f"tcp://[::1]:{port}", "--timeout", "2", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res2.returncode == 0, f"stdout: {res2.stdout}, stderr: {res2.stderr}"
    data2 = json.loads(res2.stdout)
    assert data2["ok"] is True
    assert data2["kind"] == "tcp"

    server.close()


if __name__ == "__main__":
    test_healthcheck_parses_and_connects_ipv6()
    print("test_healthcheck_ipv6: OK")
