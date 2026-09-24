"""Cross-platform reviewed-release and launch integration contract."""
import csv
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReleaseContract(unittest.TestCase):
    def test_manifest_has_one_stable_version_and_unique_supported_assets(self):
        with (ROOT / 'installers/lib/opencode-release.tsv').open() as file:
            rows = list(csv.DictReader(file, delimiter='\t'))
        self.assertEqual({r['version'] for r in rows}, {'1.18.32'})
        self.assertEqual(len({r['asset'] for r in rows}), len(rows))
        self.assertEqual({(r['platform'], r['arch']) for r in rows}, {
            ('Linux', 'x86_64'), ('Linux', 'aarch64'),
            ('Linux-musl', 'x86_64'), ('Linux-musl', 'aarch64'),
            ('Darwin', 'arm64'), ('Darwin', 'x86_64'),
            ('Windows', 'AMD64'), ('Windows', 'ARM64'),
        })
        for row in rows:
            self.assertRegex(row['sha256'], r'^[0-9a-f]{64}$')
        for file in ['installers/macos/lib/constants.sh', 'installers/windows/lib/constants.ps1']:
            value = re.search(r'OPENCODE_VERSION\s*=\s*"([^"]+)"', (ROOT / file).read_text(encoding='utf-8'))
            self.assertEqual(value[1], rows[0]['version'])

    def test_all_platform_launchers_enable_provider_scoped_search(self):
        for file in ['opencode/opencode-web.service', 'installers/macos/install-macos.sh',
                     'installers/windows/phases/07-devtools.ps1']:
            text = (ROOT / file).read_text(encoding='utf-8')
            self.assertIn('OPENCODE_ENABLE_EXA', text)
            self.assertIn('OPENCODE_WEBSEARCH_PROVIDER', text)
        for file in ['installers/phases/07-devtools.sh', 'installers/macos/install-macos.sh']:
            text = (ROOT / file).read_text(encoding='utf-8')
            self.assertIn('ods_install_opencode', text)
            self.assertNotIn('https://opencode.ai/install', text)


if __name__ == '__main__':
    unittest.main()
