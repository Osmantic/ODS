import os
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / 'bin'))
from pixel_launchd_environment import clean_gateway_document


class CleanEnvironmentTests(unittest.TestCase):
    def document(self):
        return {'Label': 'com.ods.fixture', 'ProgramArguments': ['/usr/bin/env'],
                'EnvironmentVariables': {'HOME': '/tmp/ODS home', 'PATH': '/usr/bin:/bin',
                                        'OPENCLAW_STATE_DIR': '/tmp/ODS state',
                                        'OPENCLAW_CONFIG_PATH': '/tmp/config.json'}}

    def test_real_child_has_only_declared_environment(self):
        doc = self.document()
        doc['ProgramArguments'] = ['/bin/sh', '-c', 'exec /usr/bin/env']
        converted = clean_gateway_document(doc)
        inherited = dict(os.environ, SSH_AUTH_SOCK='/tmp/not-for-pixel',
                         NODE_OPTIONS='--require=/tmp/not-for-pixel',
                         HTTPS_PROXY='http://not-for-pixel.invalid')
        output = subprocess.check_output(converted['ProgramArguments'],
                                         env=inherited, text=True)
        actual = dict(line.split('=', 1) for line in output.splitlines())
        for key, value in doc['EnvironmentVariables'].items():
            self.assertEqual(actual[key], value)
        for key in ('SSH_AUTH_SOCK', 'NODE_OPTIONS', 'HTTPS_PROXY'):
            self.assertNotIn(key, actual)
        self.assertIn('EnvironmentVariables', doc)
        self.assertNotIn('EnvironmentVariables', converted)

    def test_rejects_unapproved_or_ambiguous_launch(self):
        for change in ('unknown', 'missing', 'relative', 'program', 'wrapped'):
            doc = self.document()
            doc['ProgramArguments'] = ['/usr/local/bin/node', '/opt/gateway.mjs']
            if change == 'unknown':
                doc['EnvironmentVariables']['NODE_OPTIONS'] = '--inspect'
            elif change == 'missing':
                del doc['EnvironmentVariables']['HOME']
            elif change == 'relative':
                doc['ProgramArguments'][0] = 'node'
            elif change == 'program':
                doc['Program'] = '/other/node'
            else:
                doc['ProgramArguments'][0] = '/usr/bin/env'
            with self.subTest(change=change), self.assertRaises(ValueError):
                clean_gateway_document(doc)


if __name__ == '__main__':
    unittest.main()
