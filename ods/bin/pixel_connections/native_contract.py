"""Content-free reviewed-app wire contract; mirrored exactly in Pixel Edge.

The browser selects only a release-reviewed app, never an MCP URL, registry
entry, executable, permission set or owner transaction. Root owns those choices.
"""
import re
from urllib.parse import urlsplit


APP_IDS = ('notion',)
OPERATIONS = ('connection-app-status', 'connection-app-begin', 'connection-app-complete',
              'connection-app-finish', 'connection-app-cancel', 'connection-app-disconnect', 'connection-app-check')
TIMEOUTS = {name: 310 for name in OPERATIONS}
TIMEOUTS.update({'connection-app-status': 25, 'connection-app-complete': 80, 'connection-app-check': 80})
PHASES = ('absent', 'preparing', 'prepared', 'beginning', 'consent', 'authorized', 'claiming',
          'activating', 'verifying', 'ready', 'configured', 'withdrawing', 'forgetting', 'disconnected', 'cancelled', 'failed')
SCOPE = 'pixel-owner-default'
_FLOW = re.compile(r'[a-f0-9]{32}')
_ERROR = re.compile(r'[a-z][a-z0-9-]{0,95}')
_INSTANT = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z')
_PRINTABLE = re.compile(r'[\x21-\x7e]{1,8192}')


def _matches(value, pattern):
    return type(value) is str and pattern.fullmatch(value) is not None


def valid_callback(value):
    if not _matches(value, _PRINTABLE):
        return False
    try:
        parsed = urlsplit(value)
        return (parsed.scheme == 'http' and parsed.hostname == '127.0.0.1'
                and parsed.username is None and parsed.password is None and not parsed.fragment
                and '#' not in value and parsed.path == '/' and parsed.port is not None and 1024 <= parsed.port <= 65535
                and bool(parsed.query))
    except ValueError:
        return False


def valid_authorization_url(value):
    # Root has already bound PKCE, redirect and state to the selected native
    # server. Accept the native adapter's HTTPS or controlled loopback contract.
    if not _matches(value, _PRINTABLE) or len(value) > 7000:
        return False
    try:
        parsed = urlsplit(value)
        return (parsed.scheme in ('https', 'http') and bool(parsed.hostname)
                and (parsed.scheme == 'https' or parsed.hostname == '127.0.0.1')
                and parsed.username is None and parsed.password is None and not parsed.fragment
                and (parsed.port is None or 1 <= parsed.port <= 65535) and bool(parsed.query))
    except ValueError:
        return False


def valid_action(value):
    if (type(value) is not dict or set(value) != {'operation', 'request'}
            or value.get('operation') not in OPERATIONS or type(value.get('request')) is not dict):
        return False
    operation, request = value['operation'], value['request']
    if operation == 'connection-app-status':
        return request == {}
    if request.get('app') not in APP_IDS:
        return False
    keys = {'app'}
    if operation == 'connection-app-begin':
        return (set(request) == keys | {'redirectPort'} and type(request['redirectPort']) is int
                and 1024 <= request['redirectPort'] <= 65535)
    if operation in ('connection-app-complete', 'connection-app-finish', 'connection-app-cancel'):
        keys.add('flowId')
        if not _matches(request.get('flowId'), _FLOW):
            return False
    if operation == 'connection-app-complete':
        keys.add('address')
        if not valid_callback(request.get('address')):
            return False
    return set(request) == keys


def public_state(value):
    keys = {'app', 'phase', 'flowId', 'error', 'portalAccessVerified', 'scope'}
    if (type(value) is not dict or not keys <= set(value) <= keys | {'checkedAt', 'providerRevoked'}
            or value.get('app') not in APP_IDS or value.get('phase') not in PHASES
            or value.get('scope') != SCOPE or type(value.get('portalAccessVerified')) is not bool
            or value.get('error') is not None and not _matches(value['error'], _ERROR)
            or value['phase'] == 'absent' and value['flowId'] is not None
            or value['flowId'] is None and value['phase'] not in ('absent', 'configured', 'ready')
            or value['flowId'] is not None and not _matches(value['flowId'], _FLOW)
            or value['portalAccessVerified'] != (value['phase'] == 'ready')
            or ('checkedAt' in value) != value['portalAccessVerified']
            or 'checkedAt' in value and not _matches(value['checkedAt'], _INSTANT)
            or ('providerRevoked' in value) != (value['phase'] == 'disconnected')
            or 'providerRevoked' in value and value['providerRevoked'] is not False
            or value['phase'] in ('absent', 'ready', 'disconnected', 'cancelled') and value['error'] is not None
            or value['phase'] in ('failed', 'configured') and value['error'] is None):
        raise ValueError('invalid-native-app-state')
    return value


def public_reply(operation, value):
    if operation not in OPERATIONS or type(value) is not dict:
        raise ValueError('invalid-native-app-reply')
    if operation == 'connection-app-status':
        if (set(value) != {'schemaVersion', 'available', 'surface', 'reason', 'apps'}
                or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
                or type(value['available']) is not bool
                or value['surface'] not in ('linux-systemd', 'wsl-systemd', 'linux', 'darwin', 'windows')
                or value['reason'] is not None and not _matches(value['reason'], _ERROR)
                or type(value['apps']) is not dict
                or set(value['apps']) != (set(APP_IDS) if value['available'] else set())
                or value['available'] and value['reason'] is not None
                or not value['available'] and value['reason'] is None):
            raise ValueError('invalid-native-app-status')
        for app, entry in value['apps'].items():
            if public_state(entry)['app'] != app:
                raise ValueError('invalid-native-app-status')
        return value
    if operation == 'connection-app-begin' and value.get('phase') in ('consent', 'authorized'):
        if set(value) != {'app', 'phase', 'flowId', 'error', 'portalAccessVerified', 'scope', 'authUrl', 'expiresAt'}:
            raise ValueError('invalid-native-app-begin')
        state = {key: item for key, item in value.items() if key not in ('authUrl', 'expiresAt')}
        public_state(state)
        if (not _matches(value['expiresAt'], _INSTANT) or value['error'] is not None
                or (not valid_authorization_url(value['authUrl']) if value['phase'] == 'consent' else value['authUrl'] is not None)):
            raise ValueError('invalid-native-app-begin')
        return value
    allowed = {'connection-app-begin': ('failed',), 'connection-app-complete': ('authorized', 'failed'),
               'connection-app-cancel': ('cancelled',), 'connection-app-disconnect': ('withdrawing', 'disconnected'),
               'connection-app-check': ('ready', 'configured')}
    if (operation in allowed and value.get('phase') not in allowed[operation]
            or operation == 'connection-app-finish' and value.get('phase') in ('absent', 'authorized')):
        raise ValueError('invalid-native-app-outcome')
    return public_state(value)


def unavailable(surface):
    return public_reply('connection-app-status', {'schemaVersion': 1, 'available': False,
        'surface': surface, 'reason': 'not-available-on-this-system', 'apps': {}})
