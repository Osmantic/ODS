"""Check the same authenticated availability projection used by Portal."""
import json
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def verify(root):
    values = {}
    for line in (Path(root) / '.env').read_text(encoding='utf-8').splitlines():
        name, separator, value = line.partition('=')
        if separator and name in ('DASHBOARD_API_PORT', 'DASHBOARD_API_KEY'):
            values[name] = value.strip().strip('\"\x27')
    port = values.get('DASHBOARD_API_PORT', '3002')
    if not re.fullmatch(r'[0-9]{1,5}', port) or not 1 <= int(port) <= 65535:
        raise ValueError('Invalid DASHBOARD_API_PORT')
    key = values.get('DASHBOARD_API_KEY', '')
    if not key or '\r' in key or '\n' in key:
        raise ValueError('Missing or invalid DASHBOARD_API_KEY')
    # Do not send local credentials through environment proxies or redirects.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(
        f'http://127.0.0.1:{port}/api/pixel/status',
        headers={'Authorization': f'Bearer {key}', 'Accept': 'application/json'},
    )
    with opener.open(request, timeout=15) as response:
        payload = response.read(65537)
        if len(payload) > 65536:
            raise ValueError('Portal status response exceeds limit')
        status = json.loads(payload)
    if not isinstance(status, dict) or status.get('available') is not True:
        raise ValueError('Portal reports that its agent or model is unavailable')


if __name__ == '__main__':
    try:
        verify(sys.argv[1])
    except (OSError, ValueError, urllib.error.URLError, IndexError):
        # Never print the request, API key, response body or environment.
        print('Portal API verification failed. Check dashboard-api, Pixel configuration and model readiness; do not share API keys.', file=sys.stderr)
        raise SystemExit(1)
    print('Portal API confirms the owner agent is available. A chat test is still required to verify generation.')
