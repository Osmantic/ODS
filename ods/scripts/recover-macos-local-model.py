#!/usr/bin/env python3
"""Inspect a held Mac model switch; --apply requests verified current-model repair."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import sys
import urllib.error
import urllib.request


class RecoveryError(RuntimeError):
    """A safe, actionable diagnostic that contains no private response body."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the installation's credential away from its host agent.
        return None


def installed_env(install_dir):
    path = install_dir / 'bin/ods-host-agent.py'
    spec = importlib.util.spec_from_file_location('ods_repair_host', path)
    if spec is None or spec.loader is None:
        raise RecoveryError('The installed host agent is missing or invalid')
    host = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = host
    spec.loader.exec_module(host)
    if not callable(getattr(host, 'load_env', None)):
        raise RecoveryError('The installed host agent cannot load environment configuration')
    return host.load_env(install_dir / '.env')


def _unique_error_object(pairs):
    value = {}
    for name, item in pairs:
        if name in value:
            raise ValueError('Duplicate error field')
        value[name] = item
    return value


def request(opener, port, key, route, body=None):
    req = urllib.request.Request(
        f'http://127.0.0.1:{port}{route}',
        headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'},
        data=None if body is None else json.dumps(body).encode())
    try:
        response = opener.open(req, timeout=400 if body is not None else 10)
    except urllib.error.HTTPError as error:
        # Only exact public refusals from this repair endpoint are classified.
        # Unknown/private bodies stay suppressed, and no mutation is retried.
        code = error.code
        value = None
        try:
            if code == 409 and body is not None and route == '/v1/model/recover/current-local':
                raw = error.read(65537)
                if len(raw) <= 65536:
                    value = json.loads(raw, object_pairs_hook=_unique_error_object)
        except (OSError, ValueError, RecursionError):
            pass
        finally:
            error.close()
        if value in ({'error': 'Model lifecycle is busy'},
                     {'error': 'Model lifecycle is busy', 'code': 'model_lifecycle_busy'}):
            raise RecoveryError(
                'Host returned HTTP 409: another model operation is in progress; '
                'this repair request was not started. Inspect the saved state '
                'before requesting another repair') from None
        if (value == {'pending': True, 'phase': 'unavailable',
                      'reason': 'current-local-model-repair-unconfirmed'}
                and value['pending'] is True):
            raise RecoveryError(
                'Host returned HTTP 409: repair completion is unconfirmed and native state '
                'may already have changed. Inspect the saved state before any further repair') from None
        raise RecoveryError(f'Host returned HTTP {code}; inspect the saved state before retrying') from None
    with response:
        if response.status != 200:
            raise RecoveryError(f'Host returned HTTP {response.status}; completion is unconfirmed')
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError('Host recovery response exceeds the allowed size')
    value = json.loads(raw)
    if not isinstance(value, dict) or type(value.get('pending')) is not bool:
        raise ValueError('Host recovery response is invalid')
    transaction = value.get('transactionId')
    if transaction is not None and (not isinstance(transaction, str)
            or re.fullmatch('[a-f0-9]{64}', transaction) is None):
        raise ValueError('Host transaction identity is invalid')
    phases = {'idle', 'completed', 'prepared', 'held', 'applying', 'applied',
              'committing', 'rolling-back', 'unavailable'}
    phase = value.get('phase')
    if (not isinstance(phase, str) or phase not in phases
            or value['pending'] != (phase not in {'idle', 'completed'})
            or (phase not in {'idle', 'unavailable'} and transaction is None)
            or value.get('outcome') not in (None, 'commit', 'rollback')):
        raise ValueError('Host recovery receipt is invalid')
    return value


def run(install_dir, apply=False):
    if platform.system() != 'Darwin' or os.geteuid() == 0:
        raise RecoveryError('Run as the installing Mac user, without sudo')
    env = installed_env(install_dir)
    if env.get('GPU_BACKEND') != 'apple' or env.get('ODS_MODE') != 'local':
        raise RecoveryError('This repair only supports native Mac local inference')
    key = env.get('ODS_AGENT_KEY') or env.get('DASHBOARD_API_KEY')
    if not key:
        raise RecoveryError('The installed host credential is missing')
    port = int(env.get('ODS_AGENT_PORT') or 7710)
    if not 1 <= port <= 65535:
        raise RecoveryError('The installed host port is invalid')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    state = request(opener, port, key, '/v1/model/recovery')
    transaction = state.get('transactionId')
    phase = state.get('phase')
    if state['pending']:
        if phase not in {'held', 'applying', 'applied', 'committing'} or transaction is None:
            raise RecoveryError('This transaction requires a different recovery; no repair was sent')
    elif phase not in {'idle', 'completed'}:
        raise ValueError('Host recovery phase is inconsistent')
    if apply and state['pending']:
        state = request(opener, port, key, '/v1/model/recover/current-local',
                        {'transactionId': transaction})
        if (state['pending'] or state.get('phase') != 'completed'
                or state.get('transactionId') != transaction or state.get('outcome') != 'commit'):
            raise RecoveryError('Repair completion is unconfirmed; inspect the saved state before retrying')
    return {name: state.get(name) for name in ('pending', 'phase', 'transactionId', 'outcome')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', type=Path, default=Path.home() / 'ods')
    parser.add_argument('--apply', action='store_true',
                        help='Prove current inference and repair the existing native hold')
    args = parser.parse_args()
    try:
        result = run(args.install_dir.expanduser().resolve(), args.apply)
    except RecoveryError as error:
        print(f'Recovery stopped: {error}. Keep all saved state.', file=sys.stderr)
        return 1
    except (OSError, ValueError, RuntimeError, urllib.error.URLError,
            ImportError, AttributeError, SyntaxError, TypeError) as error:
        print(f'Recovery stopped ({type(error).__name__}). Keep all saved state. '
              'Check the local host-agent log; do not retry an unconfirmed operation.', file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
