"""Exercise the actual terminal nginx location with a local, isolated image."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid


def block(source, marker):
    start = source.index(marker)
    opening = source.index("{", start + len(marker))
    depth = 1
    for end in range(opening + 1, len(source)):
        depth += (source[end] == "{") - (source[end] == "}")
        if depth == 0:
            return source[start:end + 1]
    raise AssertionError("Unclosed nginx block")


@unittest.skipUnless(os.environ.get("ODS_NGINX_TEST_IMAGE"), "Requires a local nginx image")
class ApprovalTerminalProxy(unittest.TestCase):
    def test_browser_scheme_and_auth_survive_https_termination(self):
        source = (Path(__file__).resolve().parents[1] / "extensions/services/dashboard/nginx.conf").read_text(encoding="utf-8")
        gate = block(source, "location = /_ods_dashboard_gate")
        marker = "location = /api/pixel/approval-terminal"
        # The original generic route is exercised when the specific route is
        # absent, so this regression demonstrates the actual previous failure.
        route = block(source, marker if marker in source else "location /api/")
        route = route.replace("${DASHBOARD_API_KEY}", "test-only")
        scheme = block(source, "map $http_x_forwarded_proto $ods_cookie_scheme")
        config = """pid /tmp/approval-proxy-qa.pid;
events {}
http {
client_body_temp_path /tmp/approval-proxy-body;
proxy_temp_path /tmp/approval-proxy-cache;
""" + scheme + """
server { listen 8080;
set $ods_trusted_local 0;
set $dashboard_api_upstream 127.0.0.1:8081;
""" + gate + route + """
}
server { listen 8081;
location = /api/auth/dashboard-session/verify {
if ($http_cookie != "qa=valid") { return 401; }
return 204;
}
location = /api/pixel/approval-terminal {
default_type application/json;
return 200 '{"scheme":"$http_x_forwarded_proto","origin":"$http_origin","host":"$http_host","site":"$http_sec_fetch_site","authorization":"$http_authorization"}';
}
}
}
"""
        name = "ods-approval-proxy-qa-" + uuid.uuid4().hex[:12]

        def docker(*args, check=True):
            return subprocess.run(["docker", *args], check=check, capture_output=True, text=True)

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "nginx.conf"
            path.write_text(config, encoding="utf-8")
            docker("create", "--name", name, "--network", "none", "--entrypoint", "nginx",
                   os.environ["ODS_NGINX_TEST_IMAGE"], "-c", "/tmp/approval-proxy-qa.conf", "-g", "daemon off;")
            try:
                docker("cp", str(path), name + ":/tmp/approval-proxy-qa.conf")
                docker("start", name)

                def request(forwarded="https", cookie="qa=valid"):
                    args = ["exec", name, "wget", "-S", "-O", "-", "--post-data={}",
                            "--header", "Cookie: " + cookie,
                            "--header", "Host: dashboard.example:8443",
                            "--header", "Origin: https://dashboard.example:8443",
                            "--header", "Sec-Fetch-Site: same-origin"]
                    if forwarded is not None:
                        args += ["--header", "X-Forwarded-Proto: " + forwarded]
                    return docker(*args, "http://127.0.0.1:8080/api/pixel/approval-terminal", check=False)

                accepted = request()
                self.assertEqual(accepted.returncode, 0, accepted.stderr)
                self.assertEqual(json.loads(accepted.stdout), {
                    "scheme": "https", "origin": "https://dashboard.example:8443",
                    "host": "dashboard.example:8443", "site": "same-origin",
                    "authorization": "Bearer test-only",
                })
                for value in (None, "http", "https,http", "unexpected"):
                    with self.subTest(forwarded=value):
                        response = request(value)
                        self.assertEqual(response.returncode, 0, response.stderr)
                        self.assertEqual(json.loads(response.stdout)["scheme"], "http")
                rejected = request(cookie="qa=invalid")
                self.assertRegex(rejected.stderr, r"401\s+Unauthorized")
            finally:
                docker("rm", "-f", name, check=False)


if __name__ == "__main__":
    unittest.main()
