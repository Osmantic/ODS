"""Opt-in Portal acceptance: creates test conversations and one HTML preview.

This is not recovery or an installer-completion receipt. It never repeats a
request, installs dependencies, or deletes partial work after a failed test.
"""
import argparse
import http.client
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit
import uuid


HERE = Path(__file__).resolve().parent
LIMIT = 1024 * 1024


def load(name):
    spec = importlib.util.spec_from_file_location('acceptance_' + name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def preview_target(text, port):
    for value in re.findall(r'http://[^\s<>"\)]+', text):
        try:
            parsed = urlsplit(value)
            hostname = parsed.hostname or ''
            if (parsed.scheme != 'http' or parsed.port != port or parsed.username or parsed.password
                    or not re.fullmatch(r'site-[a-f0-9]{1,64}\.localhost', hostname)
                    or not parsed.path.startswith('/' + hostname[:-10] + '/')
                    or parsed.query or parsed.fragment or len(parsed.path) > 2048
                    or any(ord(c) < 33 or ord(c) > 126 for c in parsed.path)):
                continue
            return parsed.netloc, parsed.path
        except ValueError:
            continue
    return None


def _worker(payload):
    port, key, identity = payload['port'], payload['key'], payload['chatId']
    mode, marker = payload['mode'], payload['marker']
    if (type(port) is not int or not 1 <= port <= 65535
            or not re.fullmatch('[a-f0-9]{64}', key)
            or not re.fullmatch('[a-f0-9]{32}', identity)
            or not re.fullmatch('ODS_ACCEPT_[a-f0-9]{32}', marker)
            or mode not in ('chat', 'preview', 'cancel')
            or type(payload['previewPort']) is not int or not 1 <= payload['previewPort'] <= 65535):
        raise ValueError('invalid-test-request')
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=900)
    try:
        body = {'chat_id': identity}
        if mode != 'cancel':
            prompt = ('Local installation test. Reply with exactly ' + marker + '. Do not use tools.')
            if mode == 'preview':
                prompt = ('Local installation test. Create a new directory named ' + marker +
                    ' in your workspace with index.html containing the visible heading ' + marker +
                    '. Publish this HTML using the available preview tool and return its published URL. '
                    'Do not install dependencies, use external resources, edit other files or start a server manually.')
            body['messages'] = [{'role': 'user', 'content': prompt}]
        connection.request('POST', '/api/pixel/chat/' + ('cancel' if mode == 'cancel' else 'stream'),
            body=json.dumps(body).encode(), headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError('http-failed')
        if mode == 'cancel':
            raw = response.read(4097)
            return {'aborted': len(raw) <= 4096 and json.loads(raw).get('aborted') is True}
        if not response.getheader('Content-Type', '').lower().startswith('text/event-stream'):
            raise ValueError('stream-required')
        done, errors, total, content_size, content = False, False, 0, 0, []
        while True:
            line = response.readline(LIMIT + 1)
            total += len(line)
            if len(line) > LIMIT or total > 8 * LIMIT:
                raise ValueError('stream-too-large')
            if not line:
                break
            if not line.startswith(b'data:'):
                continue
            data = line[5:].strip()
            if data == b'[DONE]':
                done = True
                break
            packet = json.loads(data)
            errors = errors or packet.get('error') is not None
            for choice in packet.get('choices', []):
                text = choice.get('delta', {}).get('content')
                if isinstance(text, str):
                    content_size += len(text.encode())
                    if content_size > LIMIT:
                        raise ValueError('answer-too-large')
                    content.append(text)
        answer = ''.join(content)
        result = {'done': done, 'passed': False}
        if not done or errors:
            return result
        if mode == 'chat':
            result['passed'] = answer.strip() == marker
            return result
        target = preview_target(answer, payload['previewPort'])
        if target is None:
            return result
        preview = http.client.HTTPConnection('127.0.0.1', payload['previewPort'], timeout=20)
        try:
            # Never resolve a model-provided hostname or forward API credentials.
            preview.request('GET', target[1], headers={'Host': target[0]})
            published = preview.getresponse()
            body = published.read(LIMIT + 1)
            result['passed'] = published.status == 200 and len(body) <= LIMIT and marker.encode() in body
            return result
        finally:
            preview.close()
    finally:
        connection.close()


def worker(payload, timeout):
    try:
        result = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve()), '--worker'],
            input=json.dumps(payload), capture_output=True, text=True, cwd='/', timeout=timeout)
        if result.returncode or len(result.stdout) > 4096:
            raise ValueError('acceptance-worker-failed')
        value = json.loads(result.stdout)
        if type(value) is not dict:
            raise ValueError('acceptance-worker-failed')
        return value
    except (OSError, ValueError, subprocess.SubprocessError):
        return {'done': False, 'passed': False}


def exercise(install_dir, *, opencode_choice=None, timeout=600, run=worker, announce=print):
    """Explicit acceptance with a private pre-dispatch record and no retries."""
    if type(timeout) is not int or not 1 <= timeout <= 900:
        raise ValueError('acceptance-timeout-invalid')
    readiness, continuation = load('pixel-native-readiness'), load('pixel-native-continuation')
    values, snapshot = continuation._saved_environment(install_dir,
        {'DASHBOARD_API_KEY', 'DASHBOARD_API_PORT', 'PIXEL_PREVIEW_PORT'}, 'duplicate-acceptance-setting')
    before = readiness.observe_apis(install_dir, opencode_choice=opencode_choice, include_services=True)
    if before['status'] != 'api-checks-passed':
        raise ValueError('acceptance-readiness-required')
    if continuation.saved_model_environment(install_dir)[1] != snapshot:
        raise ValueError('acceptance-environment-changed')
    identity = uuid.uuid4().hex
    path = install_dir / 'data/pixel-native/preparation' / ('acceptance-' + identity + '.json')
    attempts = [{'kind': mode, 'chatId': uuid.uuid4().hex, 'marker': 'ODS_ACCEPT_' + uuid.uuid4().hex}
                for mode in ('chat', 'preview')]
    report = {'status': 'running', 'installerComplete': False, 'attempts': attempts,
              'pendingVerification': list(before['pendingVerification'])}
    continuation.publish_metadata(path, (json.dumps(report) + '\n').encode(), expected=None)
    record = continuation.owned_snapshot(path)
    # The IDs and local receipt are safe to share; no credential or transcript.
    announce(json.dumps({'acceptanceId': identity, 'attempts': attempts}))
    for attempt in attempts:
        if continuation.saved_model_environment(install_dir)[1] != snapshot:
            break
        payload = {'port': readiness.port(values, 'DASHBOARD_API_PORT', 3002),
            'previewPort': readiness.port(values, 'PIXEL_PREVIEW_PORT', 9437),
            'key': values['DASHBOARD_API_KEY'], 'chatId': attempt['chatId'],
            'marker': attempt['marker'], 'mode': attempt['kind']}
        started = time.monotonic()
        result = run(payload, timeout)
        attempt.update(passed=result.get('passed') is True and result.get('done') is True, done=result.get('done') is True,
                       seconds=round(time.monotonic() - started, 1))
        if not attempt['done']:
            attempt['cancelConfirmed'] = run(dict(payload, mode='cancel'), 30).get('aborted') is True
        continuation.publish_metadata(path, (json.dumps(report) + '\n').encode(), expected=record)
        record = continuation.owned_snapshot(path)
        if not attempt['passed']:
            break
    stable = continuation.saved_model_environment(install_dir)[1] == snapshot
    try:
        after = readiness.observe_apis(install_dir, opencode_choice=opencode_choice, include_services=True) if stable else None
    except (ValueError, OSError, KeyError, subprocess.SubprocessError):
        after = None
    stable = stable and after == before
    passed = stable and all(a.get('passed') is True for a in attempts)
    report.update(status='functional-checks-passed' if passed else 'needs-attention',
                  runtimeStable=stable)
    if passed:
        report['pendingVerification'] = [name for name in report['pendingVerification']
            if name not in ('model-completion', 'portal-chat-and-preview')]
    continuation.publish_metadata(path, (json.dumps(report) + '\n').encode(), expected=record)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--run', action='store_true', help='Create test conversations and publish one test HTML page')
    parser.add_argument('--install-dir')
    parser.add_argument('--opencode-choice', choices=('enabled', 'disabled'))
    parser.add_argument('--timeout', type=int, default=600, help='Wall-clock seconds per test, 1-900; never automatically retried')
    args = parser.parse_args()
    if args.worker:
        try:
            print(json.dumps(_worker(json.loads(sys.stdin.buffer.read(16385)))))
            return 0
        except Exception:
            return 1
    if not args.run or not args.install_dir or sys.platform != 'darwin' or os.geteuid() == 0:
        parser.error('use --run --install-dir as the signed-in macOS owner, without sudo')
    try:
        result = exercise(Path(args.install_dir).expanduser().resolve(strict=True),
            opencode_choice=args.opencode_choice, timeout=args.timeout,
            announce=lambda value: print(value, flush=True))
    except (ValueError, OSError, KeyError, subprocess.SubprocessError):
        print(json.dumps({'status': 'acceptance-stopped', 'installerComplete': False,
            'guidance': 'Preserve the acceptance record and inspect its conversation IDs before another test.'}))
        return 1
    print(json.dumps(result))
    return 0 if result['status'] == 'functional-checks-passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
