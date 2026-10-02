"""Check the same authenticated availability projection used by Portal."""
import http.client
import json
from pathlib import Path
import re
import signal
import sys
import time
import urllib.error
import urllib.request


# The status route returns a fixed, nonsecret projection (available, state,
# detail). Still never echo arbitrary bytes into the installer transcript.
_UNSAFE_TEXT = re.compile(r"[^A-Za-z0-9 ._,:;()'/-]")
_STATUS_TIMEOUT_SECONDS = 30
_MODEL_PROOF_SETTLE_SECONDS = 60
_MODEL_PROOF_RETRY_SECONDS = 2


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


def fetch_status(port, key, *, timeout_seconds=_STATUS_TIMEOUT_SECONDS):
    # Do not send local credentials through environment proxies or redirects.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(
        f'http://127.0.0.1:{port}/api/pixel/status',
        headers={'Authorization': f'Bearer {key}', 'Accept': 'application/json'},
    )
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            payload = response.read(65537)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise PortalCheckFailed('dashboard-api rejected the installed DASHBOARD_API_KEY') from None
        raise PortalCheckFailed(f'dashboard-api returned HTTP {error.code}') from None
    except urllib.error.URLError:
        raise PortalCheckFailed(f'dashboard-api is not reachable on 127.0.0.1:{port}') from None
    except TimeoutError:
        raise PortalCheckFailed('dashboard-api did not answer within the readiness deadline') from None
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


def _fetch_status_before_deadline(port, key, timeout_seconds):
    # This verifier runs as a standalone WSL process. urllib's timeout is a
    # socket inactivity limit: a slow response can keep read() alive forever.
    # SIGALRM gives the whole authenticated request a real wall deadline.
    if timeout_seconds <= 0:
        raise PortalCheckFailed('Portal status did not finish within the readiness deadline')
    if not all(hasattr(signal, name) for name in ('setitimer', 'getitimer', 'SIGALRM', 'ITIMER_REAL')):
        raise PortalCheckFailed('Portal verifier cannot enforce its wall deadline')
    if signal.getitimer(signal.ITIMER_REAL)[0] > 0:
        raise PortalCheckFailed('Portal verifier cannot own its wall deadline')

    def expired(_signum, _frame):
        raise PortalCheckFailed('Portal status did not finish within the readiness deadline')

    try:
        previous = signal.signal(signal.SIGALRM, expired)
    except ValueError:
        raise PortalCheckFailed('Portal verifier cannot enforce its wall deadline') from None
    try:
        signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
        return fetch_status(port, key, timeout_seconds=timeout_seconds)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def verify(root, *, settle_seconds=_MODEL_PROOF_SETTLE_SECONDS,
           monotonic=time.monotonic, sleep=time.sleep):
    port, key = read_settings(root)
    deadline = monotonic() + settle_seconds
    last_failure = None
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            if last_failure is not None:
                raise last_failure
            raise PortalCheckFailed('Portal readiness deadline expired before the first observation')
        # Bound every request by the remaining settlement window. Each poll
        # obtains a fresh status, including a fresh physical model identity.
        timeout = min(_STATUS_TIMEOUT_SECONDS, remaining)
        status = _fetch_status_before_deadline(port, key, timeout)
        # The socket timeout is an inactivity limit, not a total transfer
        # deadline. Never accept a proof delivered after the settlement window.
        if monotonic() > deadline:
            raise PortalCheckFailed('Portal model proof arrived after the readiness deadline')
        if status.get('available') is True:
            return
        detail = _safe(status.get('detail'), 160) or 'agent or model is unavailable'
        state = _safe(status.get('state'), 40)
        last_failure = PortalCheckFailed(
            f'Portal reports: {detail}' + (f' [{state}]' if state else ''))
        # Only the model observation may need to settle after a cold start.
        # Other unavailable states, malformed responses, and transport/auth
        # failures remain immediate failures.
        if status.get('available') is not False or state != 'model_unavailable':
            raise last_failure
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise last_failure
        sleep(min(_MODEL_PROOF_RETRY_SECONDS, remaining))


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
