#!/usr/bin/env python3
"""Read-only, bounded doctor evidence for a managed remote-provider route."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from urllib import error, parse, request


def read_regular(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError('invalid-file')
        body = stream.read(limit + 1)
        if len(body) > limit:
            raise ValueError('oversized-file')
        return body.decode('utf-8')


def read_env(root):
    path = root / 'extensions/services/dashboard-api/env_values.py'
    spec = importlib.util.spec_from_file_location('doctor_env_values', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    values = {}
    for line in read_regular(root / '.env', 2 * 1024 * 1024).splitlines():
        match = re.match(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$', line)
        if match:
            values[match[1]] = module.parse_env_value(match[2])
    return values


def local_api_url(env):
    raw = env.get('ODS_DASHBOARD_API_URL') or ('http://127.0.0.1:' + (env.get('DASHBOARD_API_PORT') or '3002'))
    url = parse.urlsplit(raw)
    if (url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost', '::1')
            or url.username is not None or url.password is not None
            or url.path not in ('', '/') or url.query or url.fragment):
        raise ValueError('nonlocal-dashboard-api')
    port = url.port or 80
    if not 1 <= port <= 65535:
        raise ValueError('invalid-api-port')
    host = '[::1]' if url.hostname == '::1' else '127.0.0.1'
    return f'http://{host}:{port}/api/remote-provider/status'


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise error.HTTPError(req.full_url, code, 'redirect-refused', headers, fp)


def classify(snapshot):
    result = {'state': 'unproven', 'ready': False, 'reason': 'remote-route-not-proven'}
    if type(snapshot) is not dict:
        return result
    route = snapshot.get('routeState')
    activation = snapshot.get('activation')
    egress = snapshot.get('egress')
    capabilities = snapshot.get('capabilities')
    if not all(type(x) is dict for x in (route, activation, egress, capabilities)):
        return result
    provider = route.get('provider')
    if type(provider) is not dict:
        return result
    model = provider.get('model')
    if type(model) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/+-]{0,255}', model):
        return result
    context, maximum = provider.get('contextLength'), provider.get('maxTokens')
    if (provider.get('transport') not in ('direct', 'ssh')
            or type(provider.get('baseUrl')) is not str
            or type(context) is not int or not 16384 <= context <= 10_000_000
            or type(maximum) is not int or not 1 <= maximum <= context
            or type(provider.get('reasoning')) is not bool
            or type(activation.get('contextLength')) is not int
            or type(activation.get('maxTokens')) is not int
            or type(activation.get('reasoning')) is not bool
            or any(activation.get(key) != provider.get(key)
                   for key in ('contextLength', 'maxTokens', 'reasoning'))):
        return result
    identity = {key: provider.get(key) for key in
                ('transport', 'baseUrl', 'model', 'contextLength', 'maxTokens', 'reasoning')}
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if (snapshot.get('status') != 'ready' or route.get('mode') != 'cloud'
            or any(route.get(key) is not True for key in ('exists', 'valid', 'enabled'))
            or any(activation.get(key) is not True for key in ('valid', 'active', 'proven'))
            or activation.get('reason') != 'active_and_proven'
            or activation.get('gateway') != 'litellm-cloud' or activation.get('publicModel') != 'ods/current'
            or activation.get('routeFingerprint') != fingerprint or activation.get('model') != model
            or any(egress.get(key) is not True for key in ('reachable', 'valid', 'ready'))
            or capabilities.get('inference') is not True):
        return result
    return {'state': 'ready', 'ready': True, 'reason': 'active_and_proven',
            'model': model, 'gateway': 'litellm-cloud', 'routeFingerprint': fingerprint}


def hermes_disabled(root):
    # A dormant .env URL is not an active consumer. Keep the warning when
    # either configured Hermes or a leftover running Hermes may still use it.
    fragment = root / 'extensions/services/hermes/compose.yaml'
    if os.path.lexists(fragment) or not fragment.with_name('compose.yaml.disabled').is_file():
        return False
    try:
        reply = subprocess.run(['docker', 'ps', '--format',
                                '{{.Names}}\t{{.Label "com.docker.compose.service"}}'],
                               capture_output=True, text=True, timeout=3, check=False)
        if reply.returncode != 0:
            return False
        for row in reply.stdout.splitlines():
            name, _, service = row.partition('\t')
            if name == 'ods-hermes' or name.startswith('ods-hermes-') or service == 'hermes':
                return False
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def probe(root):
    absent = {'state': 'absent', 'ready': False, 'hermesDisabled': False}
    # This marker selects a probe, never proves the route. Legacy cloud and
    # local/paused installs keep their existing diagnostics.
    if not os.path.lexists(root / 'data/remote-provider/routing-state.json'):
        return absent
    result = {'state': 'unavailable', 'ready': False, 'reason': 'remote-status-unavailable', 'hermesDisabled': False}
    try:
        env = read_env(root)
        if str(env.get('ODS_MODE', 'local')).strip().lower() != 'cloud':
            return absent
        url = local_api_url(env)
        key = env.get('DASHBOARD_API_KEY') or read_regular(root / 'data/dashboard-api-key.txt', 8192).strip()
        if not key or '\r' in key or '\n' in key:
            raise ValueError('invalid-key')
        opener = request.build_opener(request.ProxyHandler({}), NoRedirect())
        req = request.Request(url, headers={'Authorization': 'Bearer ' + key, 'Accept': 'application/json'})
        with opener.open(req, timeout=5) as response:
            if response.status != 200:
                raise ValueError('unexpected-status')
            body = response.read(65537)
            if len(body) > 65536:
                raise ValueError('oversized-status')
            result = classify(json.loads(body))
        result['hermesDisabled'] = hermes_disabled(root)
    except (OSError, ValueError, UnicodeError, ImportError):
        pass
    return result


if __name__ == '__main__':
    result = probe(Path(sys.argv[1]))
    Path(sys.argv[2]).write_text(json.dumps(result) + '\n', encoding='utf-8')
