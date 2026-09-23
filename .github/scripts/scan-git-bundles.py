#!/usr/bin/env python3
"""Scan reachable history inside every tracked Git bundle, without checking out code."""
import json
import os
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[2]


def main():
    output = Path(os.environ.get('GITLEAKS_BUNDLE_REPORT', 'gitleaks-bundles-report.json'))
    tracked = subprocess.check_output(
        ['git', 'ls-files', '-z', '--', '*.bundle'], cwd=ROOT,
    ).decode('utf-8').split('\0')
    findings = []
    failed = False
    for relative in filter(None, tracked):
        bundle = ROOT / relative
        if bundle.is_symlink() or not bundle.is_file():
            raise SystemExit('Tracked bundle is missing or is not a regular file.')
        with tempfile.TemporaryDirectory(prefix='ods-bundle-scan-') as temp:
            checkout = Path(temp) / 'repository'
            cloned = subprocess.run([
                'git', '-c', 'protocol.file.allow=always', 'clone', '--bare', '--no-local',
                '--quiet', '--', str(bundle), str(checkout),
            ], capture_output=True, timeout=120)
            if cloned.returncode:
                raise SystemExit('Unable to extract tracked bundle: ' + relative)
            report = Path(temp) / 'report.json'
            result = subprocess.run([
                'gitleaks', 'git', str(checkout), '--log-opts=--all', '--redact',
                '--config', str(ROOT / '.gitleaks.toml'),
                '--gitleaks-ignore-path', str(ROOT / '.gitleaksignore'),
                '--report-format', 'json', '--report-path', str(report),
                '--log-level', 'error',
            ], capture_output=True, timeout=300)
            if result.returncode not in (0, 1) or not report.is_file():
                raise SystemExit('Bundle secret scan could not complete: ' + relative)
            rows = json.loads(report.read_text(encoding='utf-8'))
            for row in rows:
                row['Archive'] = relative
            findings.extend(rows)
            failed |= bool(rows) or result.returncode != 0
            print(relative + ': ' + str(len(rows)) + ' unreviewed candidates')
    output.write_text(json.dumps(findings, indent=2) + '\n', encoding='utf-8')
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
