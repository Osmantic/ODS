#!/usr/bin/env python3
"""Run the real reset command with private /tmp and a local SCP fixture.

Linux-only harness: unshare is required so the production lock and remote
temporary path never touch another reset. CI invokes this script through sudo
when unprivileged mount namespaces are disabled.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SHEPHERD = Path(__file__).resolve().parents[1] / 'memory-shepherd/memory-shepherd.sh'


def exercise():
    class ScratchArchiveTests(unittest.TestCase):
        def reset_fixture(self, remote, content, *, archive_failure=False, full_backup=False, separator="---"):
            with tempfile.TemporaryDirectory(prefix='ods-scratch-') as directory:
                root = Path(directory)
                baseline = b'# Curated baseline\n' + b'Keep durable instructions.\n' * 30
                (root / 'baseline.md').write_bytes(baseline)
                memory = root / 'MEMORY.md'
                memory.write_bytes(content)
                archive = root / 'archives'
                if archive_failure:
                    archive.write_text('A non-directory blocks archive publication.')
                conf = root / 'memory.conf'
                location = ('remote_host=fixture.invalid\nremote_user=fixture\n'
                            'remote_memory=/fixture/MEMORY.md\n') if remote else f'memory_file={memory}\n'
                conf.write_text(f'[general]\nbaseline_dir={root}\narchive_dir={archive}\n'
                                f'separator={separator}\n[fixture]\nbaseline=baseline.md\n{location}')
                binary = root / 'bin'
                binary.mkdir()
                scp = binary / 'scp'
                scp.write_text('''#!/usr/bin/env bash
set -euo pipefail
[[ "$1" == '-q' ]]
shift
if [[ "$1" == 'fixture@fixture.invalid:/fixture/MEMORY.md' ]]; then
    cp "$ODS_TEST_MEMORY" "$2"
elif [[ "$2" == 'fixture@fixture.invalid:/fixture/MEMORY.md' ]]; then
    cp "$1" "$ODS_TEST_MEMORY"
else
    exit 91
fi
''')
                scp.chmod(0o755)
                env = {**os.environ, 'MEMORY_SHEPHERD_CONF': str(conf),
                       'PATH': f'{binary}:{os.environ["PATH"]}', 'ODS_TEST_MEMORY': str(memory)}
                result = subprocess.run(['bash', str(SHEPHERD), 'fixture'], env=env,
                                        capture_output=True, text=True, timeout=15, check=False)
                if archive_failure:
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(memory.read_bytes(), content, 'Failed archive must not reset memory')
                    return
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(memory.read_bytes(), baseline)
                files = list(archive.rglob('*.md'))
                if full_backup:
                    self.assertEqual(len(files), 1, result.stdout)
                    self.assertEqual(files[0].read_bytes(), content)
                    return
                boundary = separator.encode() + b'\n'
                notes = content.split(boundary, 1)[1] if boundary in content else b''
                if notes.strip():
                    self.assertEqual(len(files), 1, result.stdout)
                    self.assertIn(notes.rstrip(b'\n'), files[0].read_bytes())
                else:
                    self.assertEqual(files, [])

        def test_scratch_horizontal_rules_do_not_discard_earlier_notes(self):
            for remote in (False, True):
                with self.subTest(remote=remote):
                    self.reset_fixture(remote, b'# Baseline\n---\nFirst important note\n---\nSecond note\n---\nFinal note')

        def test_separator_is_literal_even_with_regex_metacharacters(self):
            for remote in (False, True):
                with self.subTest(remote=remote):
                    self.reset_fixture(remote, b'# Baseline\n[scratch]\nFirst note\ns\nFinal note', separator='[scratch]')

        def test_final_note_without_newline(self):
            for remote in (False, True):
                with self.subTest(remote=remote):
                    self.reset_fixture(remote, '# Previous baseline\n---\nKeep this final note: café.'.encode())

        def test_existing_multiline_and_empty_scratch_behavior(self):
            for remote in (False, True):
                for tail in (b'One note\n', b'First note\nFinal note', b'First note\nFinal note\n', b''):
                    with self.subTest(remote=remote, tail=tail):
                        self.reset_fixture(remote, b'# Previous baseline\n---\n' + tail)

        def test_failed_archive_preserves_unterminated_memory(self):
            for remote in (False, True):
                with self.subTest(remote=remote):
                    self.reset_fixture(remote, b'# Previous baseline\n---\nMust survive archive failure',
                                       archive_failure=True)

        def test_missing_separator_keeps_full_backup(self):
            for remote in (False, True):
                with self.subTest(remote=remote):
                    self.reset_fixture(remote, b'# Existing memory\nNo separator or final newline',
                                       full_backup=True)

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ScratchArchiveTests)
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


if __name__ == '__main__':
    if sys.argv[1:] == ['--private-tmp']:
        raise SystemExit(exercise())
    if sys.platform != 'linux':
        raise SystemExit('This isolated shell regression requires Linux mount namespaces.')
    namespace = ['unshare', '--mount', '--fork']
    if os.geteuid() != 0:
        namespace += ['--user', '--map-root-user']
    raise SystemExit(subprocess.call(namespace + ['bash', '-c',
        'set -euo pipefail; mount --make-rprivate /; mount -t tmpfs tmpfs /tmp; exec "$@"',
        'bash', sys.executable, str(Path(__file__).resolve()), '--private-tmp']))
