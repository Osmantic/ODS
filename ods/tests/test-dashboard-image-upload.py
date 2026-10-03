"""Real nginx auth-subrequest regression; set ODS_NGINX_TEST_IMAGE to a local image."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid


def location(source, marker):
    start = source.index(marker)
    opening = source.index('{', start + len(marker))
    depth = 1
    for end in range(opening + 1, len(source)):
        depth += (source[end] == '{') - (source[end] == '}')
        if depth == 0:
            return source[start:end + 1]
    raise AssertionError('Unclosed location')


@unittest.skipUnless(os.environ.get('ODS_NGINX_TEST_IMAGE'), 'Requires a local nginx image')
class ImageUploadGate(unittest.TestCase):
    def test_body_limits_and_authentication(self):
        source = (Path(__file__).resolve().parents[1] / 'extensions/services/dashboard/nginx.conf').read_text(encoding='utf-8')
        gate = location(source, 'location = /_ods_dashboard_gate')
        upload = location(source, 'location ~ "^/api/pixel/images/[A-Za-z0-9_-]{1,128}(?:/img-[a-f0-9]{32})?$"')
        upload = upload.replace('${DASHBOARD_API_KEY}', 'test-only')
        config = '''pid /tmp/image-qa.pid;
events {}
http {
client_body_temp_path /tmp/image-qa-body;
proxy_temp_path /tmp/image-qa-proxy;
server { listen 8080;
set $ods_trusted_local 0;
set $dashboard_api_upstream 127.0.0.1:8081;
''' + gate + upload + '''
}
server { listen 8081; client_max_body_size 8m;
location = /api/auth/dashboard-session/verify {
if ($http_cookie != "qa=valid") { return 401; }
return 204;
}
location /api/pixel/images/ { return 201 '{"uploaded":true}'; }
}
}
'''
        name = 'ods-image-gate-qa-' + uuid.uuid4().hex[:12]
        def docker(*args, check=True):
            return subprocess.run(['docker', *args], check=check, capture_output=True, text=True)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'nginx.conf'
            path.write_text(config, encoding='utf-8')
            docker('create', '--name', name, '--network', 'none', '--entrypoint', 'nginx',
                   os.environ['ODS_NGINX_TEST_IMAGE'], '-c', '/tmp/image-qa.conf', '-g', 'daemon off;')
            try:
                docker('cp', str(path), name + ':/tmp/image-qa.conf')
                docker('start', name)
                docker('exec', name, 'sh', '-c', 'head -c 2097152 /dev/zero | tr "\\000" x > /tmp/image-qa-body.bin')
                def upload_status(cookie):
                    return docker('exec', name, 'wget', '-S', '-O', '-', '--header', 'Cookie: ' + cookie,
                                  '--post-file=/tmp/image-qa-body.bin', 'http://127.0.0.1:8080/api/pixel/images/qa', check=False)
                accepted = upload_status('qa=valid')
                self.assertIn('201 Created', accepted.stderr, accepted.stderr)
                self.assertIn('"uploaded":true', accepted.stdout)
                rejected = upload_status('qa=invalid')
                self.assertIn('401 Unauthorized', rejected.stderr, rejected.stderr)
                docker('exec', name, 'sh', '-c', 'head -c 9437184 /dev/zero | tr "\\000" x > /tmp/image-qa-body.bin')
                oversized = upload_status('qa=valid')
                self.assertIn('413', oversized.stderr, oversized.stderr)
            finally:
                docker('rm', '-f', name, check=False)


if __name__ == '__main__':
    unittest.main()
