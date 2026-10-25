"""Exercise the production Talk proxy block with real nginx, not a browser.

Set ODS_NGINX_TEST_IMAGE to a local nginx image. The upstream below is a
transport fixture; authentication and actual Talk processing need live tests.
"""
import json
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
class TalkUploadProxy(unittest.TestCase):
    def test_upload_headroom_auth_passthrough_and_upper_bound(self):
        source = (Path(__file__).resolve().parents[1] / 'extensions/services/dashboard/nginx.conf').read_text(encoding='utf-8')
        block = location(source, 'location ^~ /api/talk/')
        config = '''pid /tmp/talk-qa.pid;
events {}
http {
client_body_temp_path /tmp/talk-qa-body;
proxy_temp_path /tmp/talk-qa-proxy;
server { listen 8080;
set $dashboard_api_upstream 127.0.0.1:8081;
''' + block + '''
}
server { listen 8081; client_max_body_size 32m;
location /api/talk/ {
if ($http_cookie != "qa=valid") { return 401; }
if ($http_authorization != "") { return 403; }
return 201 '{"upstream_reached":true,"bytes":$content_length}';
}
}
}
'''
        name = 'ods-talk-upload-qa-' + uuid.uuid4().hex[:12]

        def docker(*args, check=True):
            return subprocess.run(['docker', *args], check=check, capture_output=True, text=True, timeout=60)

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'nginx.conf'
            path.write_text(config, encoding='utf-8')
            docker('create', '--name', name, '--network', 'none', '--entrypoint', 'nginx',
                   os.environ['ODS_NGINX_TEST_IMAGE'], '-c', '/tmp/talk-qa.conf', '-g', 'daemon off;')
            try:
                docker('cp', str(path), name + ':/tmp/talk-qa.conf')
                docker('start', name)

                def body(size):
                    # Raw request bytes model a file plus its multipart envelope.
                    docker('exec', name, 'sh', '-c', 'head -c ' + str(size) + ' /dev/zero | tr "\\000" x > /tmp/talk-qa-upload.bin')

                def post(endpoint, cookie):
                    return docker('exec', name, 'wget', '-S', '-O', '-',
                                  '--header', 'Cookie: ' + cookie,
                                  '--header', 'Authorization: Bearer test-only',
                                  '--post-file=/tmp/talk-qa-upload.bin',
                                  'http://127.0.0.1:8080/api/talk/' + endpoint, check=False)

                body(2 * 1024 * 1024)
                denied = post('attachment', 'qa=invalid')
                self.assertIn('401 Unauthorized', denied.stderr, denied.stderr)
                accepted = post('attachment', 'qa=valid')
                self.assertIn('201 Created', accepted.stderr, accepted.stderr)
                self.assertIn('"upstream_reached":true', accepted.stdout)
                self.assertEqual(json.loads(accepted.stdout)['bytes'], 2 * 1024 * 1024)
                body(25 * 1024 * 1024 + 1024)
                audio = post('audio-message', 'qa=valid')
                self.assertIn('201 Created', audio.stderr, audio.stderr)
                self.assertEqual(json.loads(audio.stdout)['bytes'], 25 * 1024 * 1024 + 1024)
                body(30 * 1024 * 1024)
                oversized = post('audio-message', 'qa=valid')
                self.assertIn('413', oversized.stderr, oversized.stderr)
                self.assertNotIn('"upstream_reached":true', oversized.stdout)
            finally:
                docker('rm', '-f', name, check=False)


if __name__ == '__main__':
    unittest.main()
