"""Ask real Fish for interactive completion candidates without running ODS."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

COMPLETION = Path(__file__).resolve().parents[1] / 'completions/ods.fish'


class FishCompletionTest(unittest.TestCase):
    def complete(self, line, root):
        result = subprocess.run(['fish', '--no-config', '-c', 'source "$argv[1]"; complete -C "$argv[2]"', str(COMPLETION), line], env={**os.environ, 'INSTALL_DIR': str(root)}, capture_output=True, text=True, check=True)
        self.assertEqual(result.stderr, '', result.stderr)
        return result.stdout

    def test_command_context_alias_and_saved_presets(self):
        with tempfile.TemporaryDirectory(prefix='ods fish ') as root:
            preset = Path(root) / 'presets' / 'office setup'
            preset.mkdir(parents=True)
            (preset / 'meta.txt').touch()
            (preset / 'extensions.list').touch()
            (Path(root) / 'presets' / 'incomplete').mkdir()
            self.assertIn('remote-provider', self.complete('ods remote', root))
            self.assertIn('llama-server', self.complete('ods-cli logs llama', root))
            self.assertIn('office setup', self.complete('ods preset load ', root).replace('\\ ', ' '))
            self.assertNotIn('incomplete', self.complete('ods preset load ', root))
            self.assertNotIn('office', self.complete('ods model ', root))
            self.assertIn('--dry-run', self.complete('ods gpu reassign --dry', root))
            self.assertNotIn('--dry-run', self.complete('ods gpu status --dry', root))


if __name__ == '__main__':
    unittest.main()
