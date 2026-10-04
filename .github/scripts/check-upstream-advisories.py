#!/usr/bin/env python3
"""Report advisories that affect the upstream versions ODS pins.

Each pin is read from the file that sets it; a pin that can no longer be found
fails the check, so the watch cannot silently stop covering a product. Two
sources are combined: the reviewed GitHub Advisory Database, and the upstream
repository's own published advisories, which can precede that review by weeks.

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
    # (product, file, regex for the version, ecosystem, package, upstream repository)
    ('Open WebUI (core chat UI)', 'ods/docker-compose.base.yml',
     r'ghcr\.io/open-webui/open-webui:v([0-9][0-9.]*)@', 'pip', 'open-webui', 'open-webui/open-webui'),
    ('n8n (optional workflows)', 'ods/extensions/services/n8n/compose.yaml',
     r'n8nio/n8n:([0-9][0-9.]*)@', 'npm', 'n8n', 'n8n-io/n8n'),
    ('LiteLLM (optional gateway)', 'ods/extensions/services/litellm/compose.yaml',
     r'ghcr\.io/berriai/litellm:v([0-9][0-9.]*)', 'pip', 'litellm', 'BerriAI/litellm'),
    ('OpenClaw (Pixel runtime)', 'ods/vendor/pixel/OPENCLAW-COMPATIBILITY.json',
     r'"openclaw":\s*"([0-9][0-9.]*)"', 'npm', 'openclaw', 'openclaw/openclaw'),
    ('OpenClaw (legacy opt-in extension)', 'ods/extensions/services/openclaw/compose.yaml',
     r'ghcr\.io/openclaw/openclaw:([0-9][0-9.]*)@', 'npm', 'openclaw', 'openclaw/openclaw'),
    ('OpenCode (macOS install)', 'ods/installers/macos/lib/constants.sh',
     r'OPENCODE_VERSION="([0-9][0-9.]*)"', 'npm', 'opencode-ai', None),
]
SEVERITY_ORDER = ['critical', 'high', 'medium', 'low', 'unknown']
LISTED_PER_PRODUCT = 30  # keeps the tracking issue under GitHub's body size limit


def pinned_version(path, pattern):
    match = re.search(pattern, (ROOT / path).read_text(encoding='utf-8'))
    if not match:
        raise SystemExit(f'Pin not found in {path} (pattern {pattern}); update {Path(__file__).name}')
    return match.group(1)


def get_all(url):
    """GET every page; these endpoints page with cursors in the Link header."""
    token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
    items = []
    while url:
        request = urllib.request.Request(url, headers={
            'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
            **({'Authorization': f'Bearer {token}'} if token else {})})
        with urllib.request.urlopen(request, timeout=30) as response:
            items.extend(json.load(response))
            links = re.findall(r'<([^>]+)>;\s*rel="next"', response.headers.get('Link', ''))
        url = links[0] if links else None
    return items


def parse_version(text):
    main, _, pre = text.strip().lstrip('v').partition('-')
    return tuple(int(part) for part in re.findall(r'\d+', main)), pre


def compare(left, right):
    (ln, lp), (rn, rp) = left, right
    width = max(len(ln), len(rn))
    ln, rn = ln + (0,) * (width - len(ln)), rn + (0,) * (width - len(rn))
    if ln != rn:
        return (ln > rn) - (ln < rn)
    if lp == rp:
        return 0
    if not lp or not rp:  # a release sorts after its pre-releases
        return 1 if not lp else -1
    return (lp > rp) - (lp < rp)


CONSTRAINT = r'(<=|>=|<|>|=)?\s*v?([0-9][0-9A-Za-z.+-]*)'


def in_range(version, spec):
    """Evaluate a range such as '>= 2026.6.6, < 2026.8.1' (commas optional); None if unparseable."""
    current = parse_version(version)
    spec = spec.replace(',', ' ')
    if not re.fullmatch(rf'\s*(?:{CONSTRAINT}\s*)+', spec):
        return None
    for match in re.finditer(CONSTRAINT, spec):
        operator, bound = match.group(1) or '=', parse_version(match.group(2))
        result = compare(current, bound)
        if not {'<': result < 0, '<=': result <= 0, '>': result > 0,
                '>=': result >= 0, '=': result == 0}[operator]:
            return False
    return True


def collect(ecosystem, package, version, repository):
    """Map GHSA id -> (severity, summary, url, fixed-in) from both sources."""
    found, unparsed = {}, 0
    query = urllib.parse.urlencode({'ecosystem': ecosystem, 'affects': f'{package}@{version}', 'per_page': 100})
    for advisory in get_all(f'https://api.github.com/advisories?{query}'):
        fixed = sorted({v['first_patched_version'] for v in advisory.get('vulnerabilities', [])
                        if (v.get('package') or {}).get('name') == package and v.get('first_patched_version')})
        found[advisory['ghsa_id']] = (advisory.get('severity') or 'unknown', advisory['summary'],
                                      advisory['html_url'], ', '.join(fixed) or 'none listed')
    if repository:
        url = f'https://api.github.com/repos/{repository}/security-advisories?state=published&per_page=100'
        for advisory in get_all(url):
            for vulnerability in advisory.get('vulnerabilities') or []:
                if (vulnerability.get('package') or {}).get('name') != package:
                    continue
                affected = in_range(version, vulnerability.get('vulnerable_version_range') or '')
                if affected is None:
                    unparsed += 1
                elif affected and advisory['ghsa_id'] not in found:
                    found[advisory['ghsa_id']] = (advisory.get('severity') or 'unknown', advisory['summary'],
                                                  advisory['html_url'],
                                                  vulnerability.get('patched_versions') or 'none listed')
    return found, unparsed


def main():
    fail_on = sys.argv[sys.argv.index('--fail-on') + 1] if '--fail-on' in sys.argv else None
    lines = ['# Pinned upstream versions and known advisories', '',
             'Sources: the reviewed GitHub Advisory Database and each upstream repository\'s '
             'published advisories, checked against the version ODS pins.', '',
             '| Product | Pinned | Critical | High | Medium/Low |', '|---|---|---|---|---|']
    details, notes, blocking = [], [], 0
    for product, path, pattern, ecosystem, package, repository in PINS:
        version = pinned_version(path, pattern)
        found, unparsed = collect(ecosystem, package, version, repository)
        counts = {severity: 0 for severity in SEVERITY_ORDER}
        for severity, *_ in found.values():
            counts[severity if severity in counts else 'unknown'] += 1
        lines.append(f'| {product} | `{package}` {version} (`{path}`) | {counts["critical"]} | {counts["high"]} '
                     f'| {counts["medium"] + counts["low"] + counts["unknown"]} |')
        if unparsed:
            notes.append(f'- {product}: {unparsed} repository advisory ranges could not be evaluated.')
        serious = sorted(((ghsa, *values) for ghsa, values in found.items() if values[0] in ('critical', 'high')),
                         key=lambda item: (SEVERITY_ORDER.index(item[1]), item[0]))
        if fail_on and serious:
            blocking += len(serious) if fail_on == 'high' else sum(item[1] == 'critical' for item in serious)
        if serious:
            details += ['', f'## {product}: {package} {version}', '']
            for ghsa, severity, summary, url, fixed in serious[:LISTED_PER_PRODUCT]:
                details.append(f'- [{ghsa}]({url}) {severity}: {summary} (fixed in {fixed})')
            if len(serious) > LISTED_PER_PRODUCT:
                details.append(f'- and {len(serious) - LISTED_PER_PRODUCT} more high or critical advisories.')
    report = '\n'.join(lines + ([''] + notes if notes else []) + details) + '\n'
    print(report)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as summary:
            summary.write(report)
    if blocking:
        print(f'{blocking} high or critical advisories affect pinned versions', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
