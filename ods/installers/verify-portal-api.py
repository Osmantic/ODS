"""Check the same authenticated availability projection used by Portal."""
import http.client
import json
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request


# The status route returns a fixed, nonsecret projection (available, state,
# detail). Still never echo arbitrary bytes into the installer transcript.
_UNSAFE_TEXT = re.compile(r"[^A-Za-z0-9 ._,:;()'/-]")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PortalCheckFailed(Exception):
    """A verification failure whose message is safe to print."""


def _safe(value, limit):
    if not isinstance(value, str):
        return ''
    return _UNSAFE_TEXT.sub('', value)[:limit].strip()


def read_settings(root):
    values = {}
    try:
        text = (Path(root) / '.env').read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        raise PortalCheckFailed('cannot read the installed .env') from None
    for line in text.splitlines():
        name, separator, value = line.partition('=')
        if separator and name in ('DASHBOARD_API_PORT', 'DASHBOARD_API_KEY'):
            values[name] = value.strip().strip('\"\x27')
    port = values.get('DASHBOARD_API_PORT', '3002')
    if not re.fullmatch(r'[0-9]{1,5}', port) or not 1 <= int(port) <= 65535:
        raise PortalCheckFailed('invalid DASHBOARD_API_PORT in the installed .env')
    key = values.get('DASHBOARD_API_KEY', '')
    if not key or '\r' in key or '\n' in key:
        raise PortalCheckFailed('missing or invalid DASHBOARD_API_KEY in the installed .env')
    return port, key


def fetch_status(port, key):
    # Do not send local credentials through environment proxies or redirects.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(
        f'http://127.0.0.1:{port}/api/pixel/status',
        headers={'Authorization': f'Bearer {key}', 'Accept': 'application/json'},
    )
    try:
        with opener.open(request, timeout=15) as response:
            payload = response.read(65537)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise PortalCheckFailed('dashboard-api rejected the installed DASHBOARD_API_KEY') from None
        raise PortalCheckFailed(f'dashboard-api returned HTTP {error.code}') from None
    except urllib.error.URLError:
        raise PortalCheckFailed(f'dashboard-api is not reachable on 127.0.0.1:{port}') from None
    except TimeoutError:
        raise PortalCheckFailed('dashboard-api did not answer within 15 seconds') from None
    except (ConnectionError, http.client.HTTPException):
        # Accepted, then closed or cut short: dashboard-api is (re)starting.
        raise PortalCheckFailed('dashboard-api closed the connection before answering') from None
    if len(payload) > 65536:
        raise PortalCheckFailed('Portal status response exceeds limit')
    try:
        status = json.loads(payload)
    except ValueError:
        raise PortalCheckFailed('Portal status response is not valid JSON') from None
    if not isinstance(status, dict):
        raise PortalCheckFailed('Portal status response has an unexpected shape')
    return status


def verify(root):
    status = fetch_status(*read_settings(root))
    if status.get('available') is True:
        return
    detail = _safe(status.get('detail'), 160) or 'agent or model is unavailable'
    state = _safe(status.get('state'), 40)
    raise PortalCheckFailed(f'Portal reports: {detail}' + (f' [{state}]' if state else ''))


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print('usage: verify-portal-api.py <linux-install-dir>', file=sys.stderr)
        raise SystemExit(2)
    try:
        verify(sys.argv[1])
    except PortalCheckFailed as error:
        # Messages are fixed strings or sanitized status fields: never the
        # request, API key, raw response body or environment.
        print(f'Portal API verification failed: {error}.', file=sys.stderr)
        print('Check dashboard-api, Pixel configuration and model readiness; do not share API keys.', file=sys.stderr)
        raise SystemExit(1)
    print('Portal API confirms the owner agent is available. A chat test is still required to verify generation.')
