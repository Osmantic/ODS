#!/usr/bin/env python3
"""Exercise generated source confinement against a real, disposable Docker stack.

Runs only when explicitly enabled. No ODS installation or network is reused.
The immutable-source build protocol has separate tests; this test substitutes
a pinned Python image to exercise the generated runtime security controls.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid


ODS = Path(__file__).resolve().parents[2]
IMAGE = 'python:3.11-slim@sha256:e41613d42d4891e4930f79523f93f81bbc7632584ec65e36ab055f41a800b41e'


def run(*arguments, timeout=120):
    return subprocess.run(arguments, check=True, capture_output=True,
                          text=True, timeout=timeout).stdout.strip()


@unittest.skipUnless(os.environ.get('ODS_RUN_DOCKER_SECURITY_TESTS') == '1',
                     'requires an explicitly selected disposable Docker test host')
class SourceSandboxRuntimeTests(unittest.TestCase):
    def test_generated_profile_denies_privilege_writes_and_lateral_connections(self):
        module = (ODS / 'extensions/services/pixel-agent/plugin/extension-source-recipe.mjs').as_uri()
        javascript = f'''
import {{compileSourceRecipe}} from {json.dumps(module)};
console.log(JSON.stringify(compileSourceRecipe({{
  repository:'https://github.com/example/security-fixture', commit:'a'.repeat(40),
  serviceId:'security-fixture', name:'Security fixture', port:0, cliOnly:true,
  dockerfile:'Dockerfile', command:['python','-c','print("fixture")']
}}).compose));
'''
        compose = json.loads(run('node', '--input-type=module', '-e', javascript))
        service = compose['services']['security-fixture']
        del service['build']
        service.pop('container_name', None)
        service['image'] = IMAGE
        # A second network models a core ODS service without depending on one.
        compose['networks']['core-canary'] = {'internal': True}
        compose['services']['canary'] = {
            'image': IMAGE, 'networks': ['core-canary'],
            'command': ['python', '-m', 'http.server', '8000'],
        }
        project = 'ods-security-' + uuid.uuid4().hex[:12]
        with tempfile.TemporaryDirectory(prefix='ods-source-sandbox-') as directory:
            path = Path(directory) / 'compose.json'
            path.write_text(json.dumps(compose), encoding='utf-8')
            command = ['docker', 'compose', '-p', project, '-f', str(path)]
            try:
                run('docker', 'pull', IMAGE, timeout=300)
                run(*command, 'up', '-d', 'canary')
                canary_id = run(*command, 'ps', '-q', 'canary')
                canary = json.loads(run('docker', 'inspect', canary_id))[0]
                address = next(iter(canary['NetworkSettings']['Networks'].values()))['IPAddress']
                readiness = f'''
import time, urllib.request
for attempt in range(30):
    try:
        with urllib.request.urlopen('http://{address}:8000', timeout=1) as response:
            assert response.status == 200
            break
    except OSError:
        if attempt == 29:
            raise
        time.sleep(0.2)
'''
                run('docker', 'exec', canary_id, 'python', '-c', readiness)
                probe = f'''
import errno, os, pathlib, socket
assert os.geteuid() == 65532
status = dict(line.split(':', 1) for line in pathlib.Path('/proc/self/status').read_text().splitlines() if ':' in line)
assert int(status['CapEff'].strip(), 16) == 0
assert int(status['NoNewPrivs'].strip()) == 1
try:
    pathlib.Path('/etc/ods-write-test').write_text('denied')
except OSError as error:
    assert error.errno in (errno.EROFS, errno.EACCES)
else:
    raise AssertionError('root filesystem is writable')
pathlib.Path('/tmp/ods-write-test').write_text('allowed')
for host in ({address!r}, '1.1.1.1'):
    try:
        connection = socket.create_connection((host, 8000 if host == {address!r} else 443), timeout=2)
    except OSError:
        continue
    connection.close()
    raise AssertionError('unexpected network access: ' + host)
print('source-sandbox-ok')
'''
                service['command'] = ['python', '-c', probe]
                path.write_text(json.dumps(compose), encoding='utf-8')
                run(*command, 'create', '--no-build', 'security-fixture')
                container_id = run(*command, 'ps', '-aq', 'security-fixture')
                container = json.loads(run('docker', 'inspect', container_id))[0]
                limits = container['HostConfig']
                self.assertEqual(limits['Memory'], 2 * 1024**3)
                self.assertEqual(limits['NanoCpus'], 2 * 10**9)
                self.assertEqual(limits['PidsLimit'], 256)
                self.assertTrue(limits['ReadonlyRootfs'])
                self.assertEqual(len(container['NetworkSettings']['Networks']), 1)
                network = next(iter(container['NetworkSettings']['Networks']))
                self.assertTrue(json.loads(run('docker', 'network', 'inspect', network))[0]['Internal'])
                self.assertIn('source-sandbox-ok', run('docker', 'start', '-a', container_id))
                self.assertEqual(json.loads(run('docker', 'inspect', container_id))[0]['State']['ExitCode'], 0)
            finally:
                # Scope every cleanup to the unique Compose project created here.
                run(*command, 'down', '--volumes', '--remove-orphans')


if __name__ == '__main__':
    unittest.main()
