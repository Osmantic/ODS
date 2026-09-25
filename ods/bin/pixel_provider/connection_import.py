"""Owner-confirmed connection metadata inspection; no persistence or activation.

The disposable child bounds DNS/TLS/slow response time. The key travels only on
stdin, never argv/environment/logs. It uses the existing single-hop transport.
"""
import copy
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pixel_provider.connection import (
    connection_url,
    normalize_connection,
    normalize_probe,
)
from pixel_provider.connection_transport import probe_connection
from pixel_provider.store import StoreError, decode_document

MAX_REQUEST = 65536
MAX_BUNDLE = 32768
DEADLINE_SECONDS = 20
_lock = threading.BoundedSemaphore(1)
ERRORS = frozenset({'invalid-request', 'invalid-connection', 'invalid-probe',
                    'connection-endpoint-not-confirmed', 'unsafe-connection-address',
                    'connection-unavailable', 'connection-denied', 'connection-probe-busy'})


def normalize_request(body):
    if (type(body) is not dict or set(body) != {'bundle', 'confirmedEndpoint'}
            or type(body['bundle']) is not str or len(body['bundle']) > MAX_BUNDLE
            or type(body['confirmedEndpoint']) is not str):
        raise StoreError('invalid-request')
    try:
        raw = body['bundle'].encode('utf-8')
        if len(raw) > MAX_BUNDLE:
            raise StoreError('invalid-request')
        connection = normalize_connection(decode_document(raw))
        endpoint = connection_url(body['confirmedEndpoint'])
    except (UnicodeError, ValueError, RecursionError):
        raise StoreError('invalid-connection') from None
    if endpoint != connection['baseUrl']:
        raise StoreError('connection-endpoint-not-confirmed')
    return connection, endpoint


def public_result(metadata, connection):
    # Validate even a child/transport result before projecting it; no credential
    # or arbitrary upstream fields can be added to this response.
    metadata = normalize_probe({'object': 'list', 'data': [{'id': 'ods/shared'}],
                                'ods': metadata}, connection)
    return {'schemaVersion': 1, 'endpoint': connection['baseUrl'],
            'deviceId': connection['deviceId'], 'expiresAt': connection['expiresAt'],
            'expected': copy.deepcopy(connection['expected']), 'metadata': metadata}


def inspect_connection(body):
    connection, _endpoint = normalize_request(body)
    if not _lock.acquire(blocking=False):
        raise StoreError('connection-probe-busy')
    try:
        result = subprocess.run(
            [sys.executable, '-I', '-B', str(Path(__file__).resolve())],
            input=json.dumps(body, allow_nan=False).encode('utf-8'),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env={key: value for key, value in os.environ.items()
                 if key.upper() in {'SYSTEMROOT', 'WINDIR'}},
            timeout=DEADLINE_SECONDS, check=False,
        )
        if len(result.stdout) > 8192:
            raise StoreError('invalid-probe')
        value = decode_document(result.stdout)
        if result.returncode != 0:
            code = value.get('error') if type(value) is dict and set(value) == {'error'} else None
            raise StoreError(code if code in ERRORS else 'connection-unavailable')
        return public_result(value, connection)
    except (OSError, subprocess.TimeoutExpired):
        # subprocess.run kills AND reaps its exact child after timeout.
        raise StoreError('connection-unavailable') from None
    finally:
        _lock.release()


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
        if len(raw) > MAX_REQUEST:
            raise StoreError('invalid-request')
        connection, endpoint = normalize_request(decode_document(raw))
        metadata = probe_connection(connection, confirmed_endpoint=endpoint)
        print(json.dumps(public_result(metadata, connection)['metadata'], allow_nan=False))
        return 0
    except Exception as error:  # noqa: BLE001 -- child boundary must never emit credential-bearing exceptions
        code = error.code if isinstance(error, StoreError) and error.code in ERRORS else 'connection-unavailable'
        print(json.dumps({'error': code}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
