"""Real Dashboard API -> authenticated host HTTP -> connection coordinator.

Google, systemd/gateway processes and UID custody use the existing coordinator
fixture. This qualifies serialized API/UI behavior, not installed Root authority
or actual Google consent. State is temporary; no installed owner data is used.
"""
import http.client
from http.server import ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[5]
AUDIT = Path(os.environ['ODS_QA_REPORT_DIR'])
if Path('/qa-site').is_dir():
    sys.path.append('/qa-site')  # Pure-Python pytest fixtures, after API dependencies.
sys.path[:0] = [str(ROOT / 'extensions/services/pixel-agent/tests'), str(ROOT / 'bin'),
                str(ROOT / 'extensions/services/pixel-agent/host')]
import pytest
import test_connection_coordinator as fixture
from pixel_connections import coordinator, google_app, host_api, status as connection_status
import pixel_access_client
from pixel_access_bridge import AccessError

scratch = tempfile.TemporaryDirectory(prefix='ods-connection-http-')
patches = pytest.MonkeyPatch()
original_urlopen = urllib.request.urlopen
world = fixture.world.__wrapped__(Path(scratch.name), patches)
mode = os.environ.get('ODS_QA_GOOGLE_MODE', 'legacy')
assert mode in ('legacy', 'managed')
fixture_client = {'installed': {
    'client_id': fixture.CLIENT['clientId'], 'client_secret': fixture.CLIENT['clientSecret'],
    'project_id': fixture.CLIENT['projectId'], 'auth_uri': fixture.oauth.GOOGLE_AUTH_URI,
    'token_uri': fixture.oauth.GOOGLE_TOKEN_URI, 'redirect_uris': ['http://localhost'],
}}
if mode == 'managed':
    deployment = world.record.parent / 'portal-google-app.json'
    deployment.write_text(json.dumps({'kind': google_app.KIND, 'schemaVersion': 1, 'client': fixture_client}))
    deployment.chmod(0o600)
    patches.setattr(google_app, 'PATH', deployment)
    patches.setattr(google_app, 'OWNERS', (os.getuid(),))
def provider_only(request, timeout=None):
    address = request.full_url if hasattr(request, 'full_url') else str(request)
    if address in (fixture.BROKER.GOOGLE_TOKEN_URI, fixture.BROKER.GOOGLE_REVOKE_URI):
        return world.google(request, timeout=timeout)
    return original_urlopen(request, timeout=timeout)
patches.setattr(urllib.request, 'urlopen', provider_only)
dashboard_key = secrets.token_urlsafe(40)
assert len(dashboard_key) >= 50
agent_key = secrets.token_urlsafe(40)
install = world.bridge.install
(install / '.env').write_text('ODS_MODE=local\nPIXEL_ENABLED=true\nPORTAL_CONNECTED_APPS_ENABLED=true\n')
os.environ.update(ODS_INSTALL_DIR=str(install), ODS_DATA_DIR=str(install / 'data'),
                  ODS_EXTENSIONS_DIR=str(ROOT / 'extensions/services'),
                  DASHBOARD_API_KEY=dashboard_key, ODS_AGENT_KEY=agent_key,
                  ODS_MODE='local', ODS_AGENT_HOST='127.0.0.1', NO_COLOR='1',
                  ODS_SESSION_SECRET=secrets.token_hex(32), SERVICE_HOST='127.0.0.1')
operations: list[str] = []

def request_access(operation, request=None):
    # Only the privileged transport/process boundary is simulated. Production
    # host validation, coordinator, broker and owner transaction stay intact.
    operations.append(operation)
    try:
        value = (connection_status.report(surface='linux-systemd', record_path=world.record,
                    token_path=world.paths.token, status_path=world.paths.state / 'projection/status.json',
                    lifecycle_path=Path(world.bridge.state) / 'connection.json', broker_state=world.paths.state,
                    owners=coordinator.OWNERS)
                 if operation == 'connection-status'
                 else coordinator.control(world.bridge, operation, request))
        if operation == 'connection-status' and mode == 'managed':
            value = dict(value, googleAuth=google_app.public_status())
        return 200, value
    except AccessError as error:
        return 409, {'error': str(error)}

patches.setattr(pixel_access_client, 'request_access', request_access)
spec = importlib.util.spec_from_file_location('connected_http_host_agent', ROOT / 'bin/ods-host-agent.py')
agent = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = agent
spec.loader.exec_module(agent)
setattr(agent, 'INSTALL_DIR', install)
setattr(agent, 'DATA_DIR', install / 'data')
setup = install / 'data/config'
setup.mkdir(parents=True, exist_ok=True)
(setup / 'setup-complete.json').write_text(json.dumps({'completed': True, 'fixture': True}))
setattr(agent, 'AGENT_API_KEY', agent_key)
host = ThreadingHTTPServer(('127.0.0.1', 0), agent.AgentHandler)
host_thread = threading.Thread(target=host.serve_forever, daemon=True)
host_thread.start()
os.environ['ODS_AGENT_PORT'] = str(host.server_port)

api_directory = ROOT / 'extensions/services/dashboard-api'
sys.path.insert(0, str(api_directory))
import main
import uvicorn
server = uvicorn.Server(uvicorn.Config(main.app, host='127.0.0.1', port=0, log_level='warning', access_log=False))
thread = threading.Thread(target=server.run, daemon=True)
thread.start()

def api(method, path, payload=None, *, authenticated=True):
    port = server.servers[0].sockets[0].getsockname()[1]
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=20)
    headers = {'Content-Type': 'application/json'}
    if authenticated:
        headers['Authorization'] = 'Bearer ' + dashboard_key
    try:
        connection.request(method, '/api/pixel/connections' + path,
                           None if payload is None else json.dumps(payload), headers)
        response = connection.getresponse()
        raw = response.read()
        return response.status, json.loads(raw), dict(response.getheaders())
    finally:
        connection.close()

def checked(method, path, payload=None):
    code, value, headers = api(method, path, payload)
    assert code == 200, (path, code, value)
    assert headers.get('cache-control') == 'no-store', headers
    assert not any(secret in json.dumps(value) for secret in
                   (fixture.CLIENT['clientSecret'], 'fixture-access', 'example-refresh-from-google'))
    return value

try:
    deadline = time.monotonic() + 30
    while not server.started:
        assert thread.is_alive(), 'Full Dashboard API exited during startup'
        assert time.monotonic() < deadline, 'Full Dashboard API startup timed out'
        time.sleep(.1)
    assert api('GET', '', authenticated=False)[0] == 401
    initial = checked('GET', '')
    assert initial['connectors']['google']['state'] == 'not-connected', initial
    begin_body = {'capabilities': fixture.READ_BOTH, 'cloudModels': True,
                  'ownerTimeZone': 'America/Chicago', 'loginHint': None}
    if mode == 'managed':
        assert initial['catalog'][0]['auth']['mode'] == 'managed'
        assert initial['catalog'][0]['auth']['state'] == 'configured'
        begin_body['authMode'] = 'managed'
    else:
        assert 'mode' not in initial['catalog'][0]['auth']
        begin_body['client'] = fixture_client
    flow = checked('POST', '/google/connect', begin_body)
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(flow['authUrl']).query)
    assert query['code_challenge_method'] == ['S256']
    assert query['prompt'] == ['select_account consent' if mode == 'managed' else 'consent']
    state = query['state'][0]
    redirect = query['redirect_uri'][0]
    waiting = checked('GET', '')
    journal_path = coordinator._lifecycle_path(world.bridge)
    journal_info = journal_path.stat()
    try:
        fixture.lifecycle.read(journal_path, owners=coordinator.OWNERS)
        journal_error = None
    except fixture.lifecycle.LifecycleError as error:
        journal_error = str(error)
    assert waiting['connectors']['google']['pending'] is not None, {
        'status': {'reason': waiting['reason'], 'google': waiting['connectors']['google']},
        'journalMetadata': [journal_info.st_uid, oct(journal_info.st_mode), journal_info.st_nlink, journal_info.st_size],
        'journalReadError': journal_error, 'statePath': str(world.bridge.state),
        'journalExists': coordinator._lifecycle_path(world.bridge).exists(),
        'privateFiles': [str(p.relative_to(Path(scratch.name))) for p in Path(scratch.name).rglob('connection.json')],
    }
    assert waiting['connectors']['google']['pending']['phase'] == 'consent', waiting
    forged = redirect + '/?state=' + secrets.token_urlsafe(32) + '&code=4%2Ffixture-code'
    forged_code, forged_reply, _ = api('POST', '/google/complete', {'flowId': flow['flowId'], 'address': forged})
    assert forged_code in (400, 409), forged_code
    assert forged_reply['detail']['code'] == 'sign-in-state-mismatch', forged_reply
    assert 'Google tab opened for this connection' in forged_reply['detail']['message'], forged_reply
    assert 'Start again' not in forged_reply['detail']['message'], forged_reply
    assert checked('GET', '')['connectors']['google']['pending']['phase'] == 'consent'
    callback = redirect.rstrip('/') + '/?' + urllib.parse.urlencode({'state': state, 'code': '4/fixture-code'})
    connected = checked('POST', '/google/complete', {'flowId': flow['flowId'], 'address': callback})
    assert connected['outcome'] == 'connected', connected
    deadline = time.monotonic() + 5
    while True:
        activated = checked('GET', '')
        if activated['connectors']['google']['pending'] is None:
            break
        assert time.monotonic() < deadline, activated
        time.sleep(.1)
    assert activated['connectors']['google']['account'] == 'owner@example.com', activated
    assert activated['connectors']['google']['capabilities'] == fixture.READ_BOTH, activated
    assert activated['connectors']['google']['authMode'] == ('managed' if mode == 'managed' else 'advanced'), activated
    connected_config = json.loads(world.bridge.config_path.read_text())
    read_tools = set(fixture.planner.GMAIL_TOOLS + fixture.planner.CALENDAR_READ_TOOLS)
    assert read_tools <= set(connected_config['tools']['sandbox']['tools']['allow'])
    exchanges = len([row for row in world.google.calls if row[0] == fixture.BROKER.GOOGLE_TOKEN_URI])
    replay_code, _, _ = api('POST', '/google/complete', {'flowId': flow['flowId'], 'address': callback})
    assert replay_code in (400, 409), replay_code
    assert len([row for row in world.google.calls if row[0] == fixture.BROKER.GOOGLE_TOKEN_URI]) == exchanges
    checked('POST', '/google/disconnect', {})
    deadline = time.monotonic() + 5
    while True:
        disconnected = checked('GET', '')
        google = disconnected['connectors']['google']
        if google['state'] == 'not-connected' and google['pending'] is None:
            break
        assert time.monotonic() < deadline, disconnected
        time.sleep(.1)
    assert not world.paths.token.exists()
    disconnected_config = json.loads(world.bridge.config_path.read_text())
    assert not read_tools.intersection(disconnected_config['tools']['sandbox']['tools']['allow'])
    assert read_tools <= set(disconnected_config['agents']['list'][0]['tools']['deny'])
    report = {'fullDashboardApi': True, 'realAuthenticatedHostHttp': True,
              'actualCoordinatorBrokerTransaction': True, 'forgedCallbackPreservesFlow': True,
              'singleExchange': exchanges == 1, 'disconnectDeletesToken': True, 'readGrantsWithdrawn': True,
              'simulatedBoundaries': ['Google', 'systemd/gateway processes', 'privileged transport and UID custody'],
              'installedRuntimeTouched': False, 'operations': operations}
    report.update(managedDeploymentClient=mode == 'managed', browserClientSupplied='client' in begin_body)
    (AUDIT / 'connected-http-journey-results.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report), flush=True)
finally:
    host_api._replace_listener(None)
    server.should_exit = True
    thread.join(10)
    assert not thread.is_alive(), 'API server did not stop'
    host.shutdown()
    host.server_close()
    host_thread.join(2)
    assert not host_thread.is_alive(), 'Host HTTP server did not stop'
    patches.undo()
    scratch.cleanup()
