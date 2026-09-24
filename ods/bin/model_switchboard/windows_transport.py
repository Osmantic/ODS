"""Access Windows loopback inference from WSL through Docker Desktop.

The existing dashboard container can reach host.docker.internal even when the
WSL distribution cannot reach Windows loopback. No portproxy, LAN listener or
second management daemon is needed. Only inference operations are allowed.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any

_MAX_BYTES = 1024 * 1024
_OPERATIONS = {
    ('GET', '/api/v1/health'), ('GET', '/api/v1/models'),
    ('POST', '/api/v1/load'), ('POST', '/api/v1/unload'),
    ('POST', '/api/v1/chat/completions'),
}

# Input carries credentials over stdin, never in process arguments. Disable
# environment proxies and redirects so credentials cannot leave the Windows
# host selected by Docker Desktop.
_CLIENT = r'''
import json, sys, urllib.request, urllib.error
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None
spec = json.loads(sys.stdin.buffer.read(1048577))
headers = {'Content-Type': 'application/json'}
if spec['key']:
    headers['Authorization'] = 'Bearer ' + spec['key']
request = urllib.request.Request(
    'http://host.docker.internal:' + str(spec['port']) + spec['path'],
    data=json.dumps(spec['body']).encode() if spec['body'] is not None else None,
    method=spec['method'], headers=headers)
client = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
try:
    response = client.open(request, timeout=spec['timeout'])
except urllib.error.HTTPError as error:
    response = error
with response:
    raw = response.read(1048577)
    if len(raw) > 1048576:
        raise RuntimeError('Runtime response exceeded the size limit')
    print(json.dumps({'status': response.status, 'body': raw.decode('utf-8')}))
'''


class WindowsRuntimeTransportError(RuntimeError):
    pass


def request_json(
    method: str, path: str, *, port: int, body: dict[str, Any] | None = None,
    api_key: str = '', timeout: int = 10,
) -> dict[str, Any]:
    """Make one bounded request; an uncertain mutation is never retried."""
    if (method, path) not in _OPERATIONS:
        raise ValueError('Unsupported Windows inference operation')
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('Invalid Windows inference port')
    if type(timeout) is not int or not 1 <= timeout <= 300:
        raise ValueError('Invalid Windows inference timeout')
    if not isinstance(api_key, str) or any(ord(char) < 32 or ord(char) == 127 for char in api_key):
        raise ValueError('Invalid Windows inference credential')
    if body is not None and not isinstance(body, dict):
        raise ValueError('Inference body must be an object')
    if method == 'GET' and body is not None:
        raise ValueError('Read operations cannot carry a mutation body')
    if body and body.get('stream'):
        raise ValueError('Readiness transport requires a non-streaming response')
    wire = json.dumps(dict(method=method, path=path, port=port, body=body,
                           key=api_key, timeout=timeout), allow_nan=False)
    if len(wire.encode('utf-8')) > _MAX_BYTES:
        raise ValueError('Inference request exceeded the size limit')
    try:
        result = subprocess.run(
            ['docker', 'exec', '-i', 'ods-dashboard-api', 'python3', '-c', _CLIENT],
            input=wire, capture_output=True, text=True, timeout=timeout + 5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WindowsRuntimeTransportError(
            'Windows inference request did not complete; verify runtime state before retrying'
        ) from exc
    if result.returncode != 0:
        # Do not echo child diagnostics: credentials or user input may appear.
        raise WindowsRuntimeTransportError('Docker Desktop could not reach Windows inference')
    try:
        envelope = json.loads(result.stdout)
        status = envelope['status']
        if type(status) is not int or not 100 <= status <= 599:
            raise ValueError('Invalid status')
        if not 200 <= status < 300:
            raise WindowsRuntimeTransportError(f'Windows inference returned HTTP {status}')
        payload = json.loads(envelope['body'])
        if not isinstance(payload, dict):
            raise ValueError('Expected object')
        return payload
    except (ValueError, TypeError, KeyError) as exc:
        raise WindowsRuntimeTransportError('Invalid Windows inference response') from exc
