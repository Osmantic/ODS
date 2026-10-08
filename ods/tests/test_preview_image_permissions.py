"""Exercise the real preview image with private installer build contexts."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid

HOST = Path(__file__).resolve().parents[1] / 'extensions/services/pixel-agent/host'
FILES = ('workspace_preview.py', 'unix_peer.py', 'preview_inspection.py',
         'preview_inspection_protocol.py')


@unittest.skipUnless(os.environ.get('ODS_PREVIEW_BUILD_TESTS') == '1',
                     'actual Docker builds are opt in')
class PreviewImagePermissionsTests(unittest.TestCase):
    def test_private_context_runs_with_native_and_container_owners(self):
        for backend in ('0', '1'):
            with self.subTest(buildkit=backend):
                tag = 'ods-preview-permission-test:' + uuid.uuid4().hex
                with tempfile.TemporaryDirectory(prefix='ods-preview-private-context-') as directory:
                    context = Path(directory)
                    context.chmod(0o700)
                    for name in (*FILES, 'Dockerfile.preview'):
                        path = context / name
                        path.write_bytes((HOST / name).read_bytes())
                        path.chmod(0o600)
                    def snapshot():
                        return {p.name: (p.read_bytes(), p.stat().st_mode & 0o777)
                                for p in context.iterdir()}
                    original = snapshot()
                    try:
                        build = subprocess.run(['docker', 'build', '-f', str(context / 'Dockerfile.preview'),
                            '-t', tag, str(context)], env={**os.environ, 'DOCKER_BUILDKIT': backend},
                            capture_output=True, text=True, timeout=300)
                        self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
                        self.assertEqual(snapshot(), original)
                        for user in ('501:20', '65534:65534'):
                            with self.subTest(user=user):
                                prefix = ['docker', 'run', '--rm', '--pull=never', '--network=none',
                                    '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
                                    '--pids-limit=32', '--memory=128m', '--user', user]
                                code = ('import os,stat; from pathlib import Path; '
                                    'import workspace_preview,unix_peer,preview_inspection,preview_inspection_protocol; '
                                    f'assert os.getuid()=={user.split(":")[0]}; '
                                    f'files={FILES!r}; '
                                    'assert all(stat.S_IMODE(Path("/source",f).stat().st_mode)==0o444 for f in files); '
                                    'assert all(not os.access(Path("/source",f),os.W_OK) for f in files); '
                                    'print("preview-imports-readable-nonroot")')
                                imported = subprocess.run([*prefix, '--entrypoint=python3', tag, '-c', code],
                                    capture_output=True, text=True, timeout=30)
                                self.assertEqual(imported.returncode, 0, imported.stderr)
                                self.assertEqual(imported.stdout.strip(), 'preview-imports-readable-nonroot')
                                entry = subprocess.run([*prefix, tag], capture_output=True, text=True, timeout=30)
                                self.assertEqual(entry.returncode, 1)
                                self.assertEqual(entry.stderr.strip(), 'ODS workspace preview failed')
                                self.assertEqual(snapshot(), original)
                    finally:
                        subprocess.run(['docker', 'image', 'rm', tag], capture_output=True, timeout=30)


if __name__ == '__main__':
    unittest.main()
