#!/usr/bin/env python3
"""Bounded anonymous HTTP checks of all publishable Markdown using pinned Lychee."""
import argparse
from collections import Counter
from datetime import date
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
DEADLINE_SECONDS = 1200


def markdown_inventory(root):
    names = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=root).decode('utf-8').split('\0')
    return sorted({name for name in names if name.lower().endswith('.md')
                   and not name.startswith('output/') and not {'.git', 'node_modules'}.intersection(Path(name).parts)
                   and (root / name).is_file()})


def load_exceptions(path, today=None):
    today = today or date.today()
    document = json.loads(path.read_text(encoding='utf-8'))
    if document.get('schema_version') != 1:
        raise ValueError('Unsupported external-link exception schema')
    result = {}
    for item in document['exceptions']:
        url = item['url']
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError('An exception must identify one exact credential-free HTTP(S) URL')
        if url in result or not item.get('reason', '').strip() or not item.get('owner', '').strip():
            raise ValueError(f'Duplicate or undocumented exception: {url}')
        expires = date.fromisoformat(item['expires'])
        if expires < today or (expires - today).days > 90:
            raise ValueError(f'Expired or over-90-day exception: {url}')
        if not item.get('categories') or not set(item['categories']) <= {'missing', 'access-unconfirmed', 'transient', 'transport', 'fragment'}:
            raise ValueError(f'Unsupported exception categories: {url}')
        result[url] = item
    return result


def classify(entry, timeout=False):
    status = entry.get('status', {})
    message = ' '.join(str(status.get(key, '')) for key in ('code', 'text', 'details'))
    match = re.search(r'(?<!\d)([1-5]\d\d)(?!\d)', message)
    code = int(match.group(1)) if match else None
    if code in (401, 403):
        return 'access-unconfirmed'
    if code == 429 or (code and code >= 500) or timeout or 'timeout' in message.lower() or 'timed out' in message.lower():
        return 'transient'
    if code in (404, 410):
        return 'missing'
    if 'fragment' in message.lower():
        return 'fragment'
    return 'transport'


def validate_report(raw):
    """Validate the counters/maps emitted by reviewed Lychee 0.24.2."""
    if not isinstance(raw, dict):
        raise ValueError('Lychee report must be a JSON object')
    counters = ('total', 'unique', 'successful', 'unknown', 'unsupported', 'timeouts',
                'redirects', 'remaps', 'excludes', 'errors', 'cached')
    for key in counters:
        if type(raw.get(key)) is not int or raw[key] < 0:
            raise ValueError(f'Lychee counter {key} must be a nonnegative integer')
    if not 0 < raw['unique'] <= raw['total']:
        raise ValueError('Lychee must report at least one checked URI and a valid total')
    # ResponseStats::increment_status_counters increments exactly one terminal
    # category per response; cached/redirect/remap counters overlap categories.
    terminal = ('successful', 'unknown', 'unsupported', 'timeouts', 'excludes', 'errors')
    if raw['total'] != sum(raw[key] for key in terminal):
        raise ValueError('Lychee terminal counters do not match total responses')
    for key in ('success_map', 'error_map', 'timeout_map', 'excluded_map'):
        if not isinstance(raw.get(key), dict):
            raise ValueError(f'Lychee {key} must be a per-source object')
        for source, entries in raw[key].items():
            if not isinstance(source, str) or not isinstance(entries, list):
                raise ValueError(f'Lychee {key} must contain per-source lists')
            for entry in entries:
                if (not isinstance(entry, dict) or not isinstance(entry.get('url'), str)
                        or not entry['url'] or not isinstance(entry.get('status'), dict)
                        or not isinstance(entry['status'].get('text'), str)):
                    raise ValueError(f'Lychee {key} contains an invalid response')
                if entry.get('span') is not None and not isinstance(entry['span'], dict):
                    raise ValueError(f'Lychee {key} contains an invalid source span')


def summarize(raw, exceptions):
    validate_report(raw)
    failures = []
    deferred = []
    counts = Counter()
    for kind in ('error_map', 'timeout_map'):
        for source, entries in raw.get(kind, {}).items():
            for entry in entries:
                category = classify(entry, timeout=kind == 'timeout_map')
                finding = {'source': source.replace('\\', '/'), 'url': entry['url'], 'category': category,
                           'status': entry['status'], 'line': (entry.get('span') or {}).get('line')}
                counts[category] += 1
                exception = exceptions.get(entry['url'])
                if exception and category in exception['categories']:
                    deferred.append({**finding, 'reason': exception['reason'], 'expires': exception['expires'], 'owner': exception['owner']})
                else:
                    failures.append(finding)
    # Never silently accept a changed/unrecognized report schema or hidden errors.
    reported_errors = raw.get('errors', 0) + raw.get('timeouts', 0)
    if reported_errors > len(failures) + len(deferred):
        raise ValueError('Lychee reported failures without supported per-source diagnostics')
    return {'checked_on': date.today().isoformat(), 'anonymous': True,
            'external_fragments_checked': False, 'counts': dict(counts),
            'lychee_counts': {key: raw.get(key, 0) for key in ('total', 'unique', 'successful', 'errors', 'timeouts', 'excludes', 'unsupported')},
            'failures': failures, 'reviewed_deferred': deferred}


def write_summary(output, report):
    (output / 'summary.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    lines = ['# Public external documentation links', '',
             f"Checked: {report.get('checked_on', date.today().isoformat())}; anonymous HTTP(S).",
             'External fragments are not validated; HTTP success does not prove an anchor exists.', '',
             f"Markdown inputs: {report.get('markdown_files', 'unknown')}.",
             f"Recorded unresolved findings: {len(report.get('failures', []))}; reviewed deferrals: {len(report.get('reviewed_deferred', []))}."]
    if report.get('incomplete'):
        lines.extend(['', f"Incomplete: {report['incomplete']}", 'The total unresolved count is unknown; partial observations cannot approve this gate.'])
    for title, key in [('Unresolved findings', 'failures'), ('Reviewed temporary deferrals (not verified public links)', 'reviewed_deferred')]:
        lines.extend(['', f'## {title}', ''])
        for item in report.get(key, []):
            lines.append(f"- `{item['source']}:{item.get('line') or '?'}` — `{item['url']}` — {item['category']}")
            if key == 'reviewed_deferred':
                lines.append(f"  Reason: {item['reason']} Review expires: {item['expires']}.")
    (output / 'summary.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def partial_findings(log_path):
    """Retain observed failures if the process deadline prevents a final JSON."""
    findings = []
    seen = set()
    for number, line in enumerate(log_path.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
        match = re.match(r'^\[(\d{3}|TIMEOUT|ERROR)\] (https?://\S+)', line)
        if not match or (match[1].isdigit() and 200 <= int(match[1]) < 300):
            continue
        if (match[1], match[2]) in seen:
            continue
        seen.add((match[1], match[2]))
        entry = {'url': match[2], 'status': {'text': match[1]}}
        findings.append({**entry, 'category': classify(entry, timeout=match[1] == 'TIMEOUT'),
                         'source': 'lychee.log', 'line': number, 'partial_observation': True})
    return findings


def check(executable, output, root=ROOT):
    exceptions = load_exceptions(root / '.github/external-link-exceptions.json')
    tool = json.loads((root / '.github/lychee-tool.json').read_text(encoding='utf-8'))
    version = subprocess.check_output([executable, '--version'], text=True, timeout=10).strip()
    if version != f"lychee {tool['version']}":
        raise ValueError(f"Expected lychee {tool['version']}, got {version}")
    inputs = markdown_inventory(root)
    if not inputs:
        raise ValueError('No publishable Markdown inputs')
    output.mkdir(parents=True, exist_ok=True)
    input_list = output / 'inputs.txt'
    input_list.write_text('\n'.join(inputs) + '\n', encoding='utf-8')
    raw_path = output / 'lychee.json'
    # A failed run must never reuse the previous run's apparently green report.
    raw_path.unlink(missing_ok=True)
    environment = dict(os.environ)
    for key in ('GITHUB_TOKEN', 'GH_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN'):
        environment.pop(key, None)
    command = [executable, '--config', str(root / '.github/lychee.toml'), '--files-from', str(input_list),
               '--format', 'json', '--output', str(raw_path)]
    started = time.monotonic()
    incomplete = None
    returncode = None
    with (output / 'lychee.log').open('w', encoding='utf-8') as log:
        try:
            result = subprocess.run(command, cwd=root, env=environment, stdout=log, stderr=subprocess.STDOUT, timeout=DEADLINE_SECONDS)
            returncode = result.returncode
            if result.returncode not in (0, 1, 2):
                incomplete = f'Lychee exited with {result.returncode}; inspect lychee.log'
        except subprocess.TimeoutExpired:
            incomplete = f'Stopped after {DEADLINE_SECONDS} seconds; no public-access conclusion'
    report = {'failures': [], 'reviewed_deferred': []}
    if raw_path.exists():
        try:
            raw = json.loads(raw_path.read_text(encoding='utf-8'))
            report = summarize(raw, exceptions)
            if returncode and not report['failures'] and not report['reviewed_deferred']:
                incomplete = f'Lychee exited with {returncode} without classifiable findings'
            if raw.get('unknown') or raw.get('unsupported'):
                incomplete = 'Lychee reported unknown or unsupported results; inspect the raw report'
        except (ValueError, TypeError, KeyError) as error:
            incomplete = f'Unusable Lychee report: {error}'
    else:
        incomplete = incomplete or 'Lychee produced no JSON report; inspect lychee.log'
        report['failures'] = partial_findings(output / 'lychee.log')
    report.update(markdown_files=len(inputs), elapsed_seconds=round(time.monotonic() - started, 2), tool=version)
    if incomplete:
        report['incomplete'] = incomplete
    write_summary(output, report)
    state = 'INCOMPLETE; total unresolved count unknown' if incomplete else 'completed'
    print(f"{len(inputs)} Markdown files; {state}; {len(report['failures'])} recorded unresolved, {len(report['reviewed_deferred'])} reviewed deferrals")
    return 2 if incomplete else int(bool(report['failures']))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lychee', default='lychee')
    parser.add_argument('--output', type=Path, default=ROOT / 'output/external-links')
    args = parser.parse_args()
    raise SystemExit(check(args.lychee, args.output.resolve()))
