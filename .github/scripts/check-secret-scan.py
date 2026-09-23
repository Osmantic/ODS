#!/usr/bin/env python3
"""Fail if the project's secret scanner stops detecting representative rules.

All canaries are synthetic, assembled only inside a disposable directory.
Never print scanner output or candidate values, even when this check fails.
"""
import json
from pathlib import Path
import subprocess
import tempfile


def main():
    root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix='ods-secret-canary-') as directory:
        fixture = Path(directory)
        payload = '\n'.join([
            'github_token = "' + 'gh' + 'p_' + 'aB3dE6gH9jK2mN5pQ8sT1vW4yZ7cF0iL3oR6' + '"',
            'gitlab_token = "' + 'gl' + 'pat-' + 'aB3dE6gH9jK2mN5pQ8sT' + '"',
            '-----BEGIN ' + 'RSA PRIVATE KEY-----',
            'SyntheticCanaryNotARealPrivateKey0123456789',
            '-----END ' + 'RSA PRIVATE KEY-----',
            'langfuse_secret = "' + 'sk' + '-lf-' + 'abc123def456' + '"',
            'ods_langfuse_secret = "' + 'sk' + '-lf-ods-' + 'abc123def456' + '"',
        ]) + '\n'
        (fixture / 'canary.txt').write_text(payload, encoding='utf-8')
        # A historical installer exception must not suppress unrelated secrets.
        installer = fixture / 'ods/installers/phases/06-directories.sh'
        installer.parent.mkdir(parents=True)
        installer.write_text(payload, encoding='utf-8')
        ignore = fixture / '.gitleaksignore'
        ignore.write_text('', encoding='utf-8')
        report = fixture / 'report.json'
        result = subprocess.run([
            'gitleaks', 'dir', str(fixture), '--config', str(root / '.gitleaks.toml'),
            '--redact', '--report-format', 'json', '--report-path', str(report),
            '--gitleaks-ignore-path', str(ignore), '--log-level', 'error',
        ], capture_output=True, timeout=60)
        if result.returncode != 1 or not report.is_file():
            raise SystemExit('Secret scanner canary failed: expected detections.')
        findings = json.loads(report.read_text(encoding='utf-8'))
        for filename in ('canary.txt', 'ods/installers/phases/06-directories.sh'):
            rules = {item['RuleID'] for item in findings
                     if item['File'].replace('\\', '/').endswith(filename)}
            expected = {'github-pat', 'gitlab-pat', 'private-key', 'langfuse-project-secret-key'}
            missing = expected - rules
            if missing:
                raise SystemExit('Secret scanner canary missed rules: ' + ', '.join(sorted(missing)))
        print('Secret scanner default/custom rules and installer-path coverage verified.')


if __name__ == '__main__':
    main()
