"""Exercise the shipped nginx template, including inherited response headers.

Uses isolated loopback ports and fixture credentials; requires nginx on PATH.
Browser interaction/isolation is covered by pixel-agent/tests/preview_browser.test.cjs.
"""
import ast
import http.client
import http.server
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time

import pytest


def test_preview_keeps_only_edge_csp_and_portal_policy_stays_strict(tmp_path):
    nginx = shutil.which("nginx")
    if not nginx:
        pytest.skip("real nginx is required for proxy header integration")
    services = Path(__file__).resolve().parents[2]
    tree = ast.parse((services / "pixel-edge/pixel_edge.py").read_text())
    edge_csp = next(ast.literal_eval(node.value) for node in tree.body
                    if isinstance(node, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "_REMOTE_PREVIEW_CSP"
                            for t in node.targets))
    observed = []

    class Edge(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            observed.append((self.path, self.headers.get("Host"),
                             self.headers.get("Authorization")))
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Security-Policy", edge_csp)
            self.end_headers()
            self.wfile.write(b"<script>document.body.dataset.ready='yes'</script>")

        def log_message(self, *args):
            pass

    edge = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Edge)
    thread = threading.Thread(target=edge.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    template = (services / "dashboard/nginx.conf").read_text()
    template = template.replace("listen 3001;", f"listen 127.0.0.1:{port};")
    template = template.replace("listen [::]:3001;", "# no IPv6 listener in fixture")
    template = template.replace("/usr/share/nginx/html", str(tmp_path))
    template = template.replace("pixel-edge:9595", f"127.0.0.1:{edge.server_port}")
    template = template.replace("${DASHBOARD_API_KEY}", "fixture-only-key")
    template = template.replace("__PIXEL_PREVIEW_PORT__", "9437")
    (tmp_path / "index.html").write_text("portal fixture")
    (tmp_path / "logs").mkdir()
    config = tmp_path / "nginx.conf"
    config.write_text(f"pid {tmp_path / 'nginx.pid'};\nerror_log stderr;\n"
                      + "events {}\nhttp { access_log off;\n" + template + "\n}\n")
    log = (tmp_path / "nginx.log").open("w+")
    process = subprocess.Popen([nginx, "-p", str(tmp_path), "-c", str(config),
                                "-g", "daemon off;"], stdout=log, stderr=log)

    def request(path):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            return response.status, response.getheaders(), response.read()
        finally:
            connection.close()

    try:
        for _ in range(100):
            assert process.poll() is None, "nginx fixture exited before readiness"
            try:
                portal = request("/")
                break
            except OSError:
                time.sleep(0.02)
        else:
            pytest.fail("nginx fixture did not become ready")
        status, headers, body = request("/pixel-preview/site-" + "a" * 24 + "/")
        assert status == 200 and b"<script>" in body
        assert [v for k, v in headers if k.lower() == "content-security-policy"] == [edge_csp]
        assert ("/preview/site-" + "a" * 24 + "/", "pixel-edge", "Bearer fixture-only-key") in observed
        assert any(k.lower() == "x-content-type-options" and v == "nosniff" for k, v in headers)
        assert any(k.lower() == "x-frame-options" and v == "SAMEORIGIN" for k, v in headers)
        assert portal[0] == 200 and portal[2] == b"portal fixture"
        policies = [v for k, v in portal[1] if k.lower() == "content-security-policy"]
        assert len(policies) == 1
        script_policy = next(p for p in policies[0].split(";") if p.strip().startswith("script-src "))
        assert "'unsafe-inline'" not in script_policy
        assert "sandbox allow-scripts allow-forms allow-downloads;" in edge_csp
        assert "allow-same-origin" not in edge_csp
        assert "connect-src 'self';" in edge_csp and "form-action 'none';" in edge_csp
    finally:
        process.terminate()
        process.wait(timeout=5)
        edge.shutdown()
        edge.server_close()
        thread.join(timeout=5)
        log.close()
