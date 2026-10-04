#!/usr/bin/env python3
"""Report GitHub advisories that affect the upstream versions ODS pins.

Each pin is read from the file that sets it; a pin that can no longer be found
fails the check, so the watch cannot silently stop covering a product.

  check-upstream-advisories.py                 print a Markdown report
  check-upstream-advisories.py --fail-on high  exit 1 if any high or critical
                                               advisory affects a pinned version
"""
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PINS = [
    # (product, file, regex for the version, ecosystem, package)
    ('Open WebUI (core chat UI)', 'ods/docker-compose.base.yml',
     r'ghcr\.io/open-webui/open-webui:v([0-9][0-9.]*)@', 'pip', 'open-webui'),
    ('n8n (optional workflows)', 'ods/extensions/services/n8n/compose.yaml',
     r'n8nio/n8n:([0-9][0-9.]*)@', 'npm', 'n8n'),
    ('LiteLLM (optional gateway)', 'ods/extensions/services/litellm/compose.yaml',
     r'ghcr\.io/berriai/litellm:v([0-9][0-9.]*)', 'pip', 'litellm'),
    ('OpenClaw (Pixel runtime)', 'ods/vendor/pixel/OPENCLAW-COMPATIBILITY.json',
     r'"openclaw":\s*"([0-9][0-9.]*)"', 'npm', 'openclaw'),
    ('OpenClaw (legacy opt-in extension)', 'ods/extensions/services/openclaw/compose.yaml',
     r'ghcr\.io/openclaw/openclaw:([0-9][0-9.]*)@', 'npm', 'openclaw'),
    ('OpenCode (macOS install)', 'ods/installers/macos/lib/constants.sh',
     r'OPENCODE_VERSION="([0-9][0-9.]*)"', 'npm', 'opencode-ai'),
]
SEVERITY_ORDER = ['critical', 'high', 'medium', 'low', 'unknown']
LISTED_PER_PRODUCT = 30  # keeps the tracking issue under GitHub's body size limit


def pinned_version(path, pattern):
    match = re.search(pattern, (ROOT / path).read_text(encoding='utf-8'))
    if not match:
        raise SystemExit(f'Pin not found in {path} (pattern {pattern}); update {Path(__file__).name}')
    return match.group(1)


def advisories(ecosystem, package, version):
    token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
    query = urllib.parse.urlencode({'ecosystem': ecosystem, 'affects': f'{package}@{version}',
                                    'per_page': 100})
    url, found = f'https://api.github.com/advisories?{query}', []
    while url:
        request = urllib.request.Request(url, headers={
            'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
            **({'Authorization': f'Bearer {token}'} if token else {})})
        with urllib.request.urlopen(request, timeout=30) as response:
            found.extend(json.load(response))
            # This endpoint pages with cursors in the Link header, not ?page=.
            links = re.findall(r'<([^>]+)>;\s*rel="next"', response.headers.get('Link', ''))
        url = links[0] if links else None
    return found


def first_patched(advisory, package):
    versions = [v.get('first_patched_version') for v in advisory.get('vulnerabilities', [])
                if (v.get('package') or {}).get('name') == package and v.get('first_patched_version')]
    return ', '.join(sorted(set(versions))) or 'none listed'


def main():
    fail_on = sys.argv[sys.argv.index('--fail-on') + 1] if '--fail-on' in sys.argv else None
    lines = ['# Pinned upstream versions and known advisories', '',
             'Source: the GitHub Advisory Database, queried for each version ODS pins.', '',
             '| Product | Pinned | Critical | High | Medium/Low |', '|---|---|---|---|---|']
    details, blocking = [], 0
    for product, path, pattern, ecosystem, package in PINS:
        version = pinned_version(path, pattern)
        found = advisories(ecosystem, package, version)
        counts = {s: 0 for s in SEVERITY_ORDER}
        for advisory in found:
            counts[advisory.get('severity') or 'unknown'] = counts.get(advisory.get('severity') or 'unknown', 0) + 1
        lines.append(f'| {product} | `{package}` {version} (`{path}`) | {counts["critical"]} | {counts["high"]} '
                     f'| {counts["medium"] + counts["low"] + counts["unknown"]} |')
        serious = [a for a in found if a.get('severity') in ('critical', 'high')]
        if fail_on and serious:
            blocking += len(serious) if fail_on == 'high' else sum(a['severity'] == 'critical' for a in serious)
        if serious:
            details += ['', f'## {product}: {package} {version}', '']
            ordered = sorted(serious, key=lambda a: (SEVERITY_ORDER.index(a['severity']), a['ghsa_id']))
            for advisory in ordered[:LISTED_PER_PRODUCT]:
                details.append(f'- [{advisory["ghsa_id"]}]({advisory["html_url"]}) {advisory["severity"]}: '
                               f'{advisory["summary"]} (fixed in {first_patched(advisory, package)})')
            if len(ordered) > LISTED_PER_PRODUCT:
                search = ('https://github.com/advisories?query='
                          + urllib.parse.quote(f'type:reviewed ecosystem:{ecosystem} {package}'))
                details.append(f'- and {len(ordered) - LISTED_PER_PRODUCT} more high or critical advisories: '
                               f'[advisory search]({search})')
    report = '\n'.join(lines + details) + '\n'
    print(report)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as summary:
            summary.write(report)
    if blocking:
        print(f'{blocking} high or critical advisories affect pinned versions', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
