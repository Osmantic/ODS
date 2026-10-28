"""Full Dashboard and host HTTP over isolated Root/owner native-app fixtures.

Native OAuth/provider, gateway services and privileged transport are simulated.
No installed runtime, real provider or account is used or contacted.
"""
import http.client
from http.server import ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit

ROOT = Path(__file__).resolve().parents[5]
AUDIT = Path(os.environ['ODS_QA_REPORT_DIR'])
sys.path[:0] = [str(ROOT / 'extensions/services/pixel-agent/tests'), str(ROOT / 'bin'),
                str(ROOT / 'extensions/services/pixel-agent/host')]
import pytest
import test_native_enrollment as fixture
import pixel_access_client
from pixel_access_bridge import AccessError
from pixel_access_protocol import control_request, decode_frame
from pixel_connections import native_access, native_host_api, native_public, native_mcp

scratch = tempfile.TemporaryDirectory(prefix='ods-native-http-')
patches = pytest.MonkeyPatch()
world = fixture.harness.world.__wrapped__(Path(scratch.name), patches)
world = fixture.owner.__wrapped__(world, patches)
edge_status = world.bridge.edge
def canonical_edge_status(*args, **kwargs):
    value = edge_status(*args, **kwargs)
    return dict(value, admission_blocked=value['phase'] in ('held', 'interrupted'))
patches.setattr(world.bridge, 'edge', canonical_edge_status)
patches.setattr(native_access, 'policy_fingerprint', lambda binding: native_mcp.digest('reviewed-sdk-fixture'))
patches.setattr(world.bridge, 'gateway_installation_binding', lambda **kwargs: world.bridge.unit_boundary())
patches.setattr(native_public, 'Controller', lambda bridge, app: world.controller)
scenario = os.environ.get('ODS_QA_NATIVE_SCENARIO', 'normal')
assert scenario in ('normal', 'lost-exchange')
dashboard_key, agent_key = secrets.token_urlsafe(40), secrets.token_urlsafe(40)
install = world.bridge.install
(install / '.env').write_text('ODS_MODE=local\nPIXEL_ENABLED=true\nPORTAL_CONNECTED_APPS_ENABLED=true\n')
os.environ.update(ODS_INSTALL_DIR=str(install), ODS_DATA_DIR=str(install / 'data'),
    ODS_EXTENSIONS_DIR=str(ROOT / 'extensions/services'), DASHBOARD_API_KEY=dashboard_key,
    ODS_AGENT_KEY=agent_key, ODS_MODE='local', ODS_AGENT_HOST='127.0.0.1', NO_COLOR='1',
    ODS_SESSION_SECRET=secrets.token_hex(32), SERVICE_HOST='127.0.0.1')
operations = []
activation_done = threading.Event()
native_worker = world.bridge.worker
lose_exchange_once = scenario == 'lost-exchange'


def worker(operation='status', **kwargs):
    global lose_exchange_once
    result = native_worker(operation, **kwargs)
    request = kwargs.get('native_oauth_request', {})
    if operation == 'native-mcp-oauth' and request.get('action') == 'complete' and lose_exchange_once:
        lose_exchange_once = False
        raise native_mcp.NativeError('native-oauth-exchange-unconfirmed')
    return result


patches.setattr(world.bridge, 'worker', worker)


def root_request(operation, request=None):
    operations.append(operation)
    try:
        control_request({'operation': operation, 'request': request})
        return 200, native_public.control(world.bridge, operation, request)
    except AccessError as error:
        return 409, {'error': error.code}
    finally:
        if operation == 'connection-app-finish':
            activation_done.set()


class RootHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(340)
        try:
            raw = self.rfile.readline(16385)
            value = control_request(decode_frame(raw.decode(), 16384))
            code, reply = root_request(value['operation'], value['request'])
        except (ValueError, TypeError, KeyError):
            code, reply = 400, {'error': 'invalid-request'}
        self.wfile.write(json.dumps({'status': code, 'body': reply}).encode() + b'\n')


socket_path = str(Path(scratch.name) / 'root-control.sock')
root_server = socketserver.ThreadingUnixStreamServer(socket_path, RootHandler)
root_server.daemon_threads = True
root_thread = threading.Thread(target=root_server.serve_forever, daemon=True)
root_thread.start()
node_key = secrets.token_urlsafe(40)
node_script = r'''
import http from 'node:http';
import {pathToFileURL} from 'node:url';
const chunks=[]; for await (const chunk of process.stdin) chunks.push(chunk);
const {module,ownerKey,socketPath}=JSON.parse(Buffer.concat(chunks).toString('utf8'));
const {handleConnections,requestAccessController}=await import(pathToFileURL(module).href);
const server=http.createServer((req,res)=>handleConnections(req,res,{ownerKey,
  request:(payload,options)=>requestAccessController(payload,{...options,socketPath})}));
server.listen(0,'127.0.0.1',()=>process.stdout.write(JSON.stringify({port:server.address().port})+'\n'));
'''
node_process = subprocess.Popen(['node', '--input-type=module', '-e', node_script], stdin=subprocess.PIPE,
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
node_process.stdin.write(json.dumps({'module': str(ROOT / 'extensions/services/pixel-agent/host/access_mode_relay.mjs'),
    'ownerKey': node_key, 'socketPath': socket_path}))
node_process.stdin.close()
node_port = json.loads(node_process.stdout.readline())['port']


def node_request(payload, *, authenticated=True):
    connection = http.client.HTTPConnection('127.0.0.1', node_port, timeout=20)
    try:
        headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + (node_key if authenticated else 'wrong')}
        connection.request('POST', '/v1/connections', json.dumps(payload), headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def request_access(operation, request=None):
    # Real owner-authenticated Node HTTP -> real framed Unix socket -> the
    # isolated Root/owner controller. Docker/UID authority remains simulated.
    return node_request({'operation': operation, 'request': request})


patches.setattr(pixel_access_client, 'request_access', request_access)
spec = importlib.util.spec_from_file_location('native_http_host_agent', ROOT / 'bin/ods-host-agent.py')
agent = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = agent
spec.loader.exec_module(agent)
agent.INSTALL_DIR, agent.DATA_DIR, agent.AGENT_API_KEY = install, install / 'data', agent_key
setup = install / 'data/config'
setup.mkdir(parents=True, exist_ok=True)
(setup / 'setup-complete.json').write_text(json.dumps({'completed': True, 'fixture': True}))
host = ThreadingHTTPServer(('127.0.0.1', 0), agent.AgentHandler)
host_thread = threading.Thread(target=host.serve_forever, daemon=True)
host_thread.start()
os.environ['ODS_AGENT_PORT'] = str(host.server_port)
sys.path.insert(0, str(ROOT / 'extensions/services/dashboard-api'))
import main
import uvicorn
server = uvicorn.Server(uvicorn.Config(main.app, host='127.0.0.1', port=0, log_level='warning', access_log=False))
thread = threading.Thread(target=server.run, daemon=True)
thread.start()


def api(method, path='', payload=None, authenticated=True):
    port = server.servers[0].sockets[0].getsockname()[1]
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=20)
    headers = {'Content-Type': 'application/json'}
    if authenticated:
        headers['Authorization'] = 'Bearer ' + dashboard_key
    try:
        connection.request(method, '/api/pixel/connections/apps' + path,
            None if payload is None else json.dumps(payload), headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read()), dict(response.getheaders())
    finally:
        connection.close()


def checked(method, path='', payload=None):
    code, value, headers = api(method, path, payload)
    assert code == 200, (path, code, value)
    assert headers.get('cache-control') == 'no-store'
    assert 'PRIVATE-native-code' not in json.dumps(value)
    # The transient authorization address intentionally includes the public
    # S256 challenge. It must not appear in durable/status or later replies.
    if method != 'POST' or path != '/notion/connect':
        assert 'native-private-pkce' not in json.dumps(value)
    return value


try:
    deadline = time.monotonic() + 30
    while not server.started:
        assert thread.is_alive() and time.monotonic() < deadline
        time.sleep(.1)
    assert api('GET', authenticated=False)[0] == 401
    assert node_request({'operation': 'connection-app-status', 'request': {}}, authenticated=False)[0] == 403
    assert checked('GET')['apps']['notion']['phase'] == 'absent'
    assert api('POST', '/notion/connect', {'server': 'https://unreviewed.example'})[0] == 422
    assert operations == ['connection-app-status']
    # Input rejection is tested at API and Node -> Root, not just native SDK
    # exceptions. Neither side reflects callbacks, JSON or extra secret fields.
    sentinel = 'PRIVATE-input-code-token'
    rejected = api('POST', '/notion/complete', {'flowId': 'a' * 32, 'address': sentinel})
    assert rejected[0] == 422 and sentinel not in json.dumps(rejected[1])
    rejected = node_request({'operation': 'connection-app-complete', 'request': {
        'app': 'notion', 'flowId': 'a' * 32, 'address': 'http://127.0.0.1:43123/?state=x&code=' + sentinel,
        'clientSecret': sentinel}})
    assert rejected[0] == 400 and sentinel not in json.dumps(rejected[1]) and world.native_calls == []
    flow = checked('POST', '/notion/connect', {})
    assert flow['phase'] == 'consent' and not flow['portalAccessVerified']
    query = parse_qs(urlsplit(flow['authUrl']).query)
    redirect, state = query['redirect_uri'][0], query['state'][0]
    assert query['code_challenge_method'] == ['S256']
    forged = redirect + '?' + urlencode({'state': 'x' * 128, 'code': 'PRIVATE-native-code' + 'c' * 2000})
    assert len(json.dumps({'flowId': flow['flowId'], 'address': forged})) > 2048
    forged_code, _, _ = api('POST', '/notion/complete', {'flowId': flow['flowId'], 'address': forged})
    assert forged_code == 409 and world.native_calls == ['begin']
    assert checked('GET')['apps']['notion']['phase'] == 'consent'
    # A forged browser redirect must keep this one-shot listener available for
    # the valid callback. A wrong pasted URL alone does not qualify this path.
    endpoint = urlsplit(forged)
    connection = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=20)
    try:
        connection.request('GET', endpoint.path + '?' + endpoint.query)
        response = connection.getresponse()
        forged_page = response.read().decode()
        assert response.status == 200 and 'another sign-in' in forged_page and 'PRIVATE-' not in forged_page
    finally:
        connection.close()
    assert checked('GET')['apps']['notion']['phase'] == 'consent' and world.native_calls == ['begin']
    callback = redirect + '?' + urlencode({'state': state, 'code': 'PRIVATE-native-code'})
    if scenario == 'lost-exchange':
        assert api('POST', '/notion/complete', {'flowId': flow['flowId'], 'address': callback})[0] == 409
        assert world.authorized and world.native_calls == ['begin', 'complete']
        assert api('POST', '/notion/complete', {'flowId': flow['flowId'], 'address': callback})[0] == 409
        failed = checked('POST', '/notion/finish', {'flowId': flow['flowId']})
        assert failed['phase'] == 'failed' and not failed['portalAccessVerified']
        assert world.native_calls == ['begin', 'complete']  # No token-presence inference or code replay.
        activation_done.clear()
        flow = checked('POST', '/notion/connect', {})  # Explicit new owner intent.
        assert flow['phase'] == 'authorized' and flow['authUrl'] is None
    else:
        endpoint = urlsplit(callback)
        connection = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=20)
        try:
            connection.request('GET', endpoint.path + '?' + endpoint.query)
            response = connection.getresponse()
            page = response.read().decode()
            assert response.status == 200 and 'Sign-in accepted' in page
            assert 'Connected' not in page and 'PRIVATE-native-code' not in page
        finally:
            connection.close()
    assert activation_done.wait(15), 'Background activation did not finish'
    ready = checked('GET')['apps']['notion']
    assert ready['phase'] == 'ready' and ready['portalAccessVerified'] is True
    assert ready['scope'] == 'pixel-owner-default'
    assert world.native_calls.count('complete') == 1 and world.native_calls.count('probe') == 1
    calls_before_status = list(world.native_calls)
    checked('GET')
    assert world.native_calls == calls_before_status
    assert fixture.choice(world) == {'notion': True}
    # Check is a fresh read-only SDK observation through every live HTTP/UDS
    # boundary. It cannot replay OAuth or mutate the authoritative admission.
    paths = (world.controller.path, world.controller.consent.path, world.record,
             world.bridge.config_path, world.bridge.env_path, world.bridge.answers_path)
    before = {path: path.read_bytes() for path in paths}
    observed = checked('POST', '/notion/check', {})
    assert observed['phase'] == 'ready' and observed['portalAccessVerified']
    assert {path: path.read_bytes() for path in paths} == before
    assert world.native_calls == calls_before_status + ['probe']
    checked('GET')
    assert world.native_calls == calls_before_status + ['probe']
    disconnected = checked('POST', '/notion/disconnect', {})
    assert disconnected['phase'] == 'disconnected' and disconnected['providerRevoked'] is False
    assert not disconnected['portalAccessVerified'] and not world.authorized
    assert fixture.choice(world) == {'notion': False}
    assert world.bridge.pending() is None and world.bridge.native_phase == world.bridge.edge_phase == 'idle'
    assert checked('GET')['apps']['notion']['phase'] == 'disconnected'
    public = json.dumps(disconnected)
    assert not any(key in public for key in ('binding', 'step', 'configSha256', 'authorizationCode'))
    (AUDIT / 'native-http-journey-results.json').write_text(json.dumps({
        'fullDashboardApi': True, 'realAuthenticatedHostHttp': True, 'realAuthenticatedNodeHttp': True,
        'realFramedUnixSocket': True, 'actualRootOwnerTransactions': True, 'invalidInputRedacted': True,
        'loopbackCallback': scenario == 'normal', 'wrongStatePreservedFlow': True,
        'singleExchange': True, 'lostExchangeStayedUnconfirmed': scenario == 'lost-exchange',
        'reusedCredentialsDidNotClaimAccountSelection': scenario == 'lost-exchange',
        'readyOnlyAfterScopedProof': True, 'statusDidNoProviderWork': True,
        'explicitCheckWasReadonly': True,
        'withdrawalVerifiedBeforeLocalForget': True, 'providerRevoked': False,
        'installedRuntimeTouched': False, 'realAccountContacted': False,
        'simulatedBoundaries': ['native OAuth/provider', 'systemd/gateway processes', 'privileged transport and UID custody'],
    }, indent=2))
finally:
    native_host_api._close_listener('notion')
    server.should_exit = True
    thread.join(timeout=10)
    host.shutdown()
    host.server_close()
    host_thread.join(timeout=2)
    node_process.terminate()
    node_process.wait(timeout=5)
    node_errors = node_process.stderr.read()
    assert 'PRIVATE-' not in node_errors
    root_server.shutdown()
    root_server.server_close()
    root_thread.join(timeout=2)
    patches.undo()
    scratch.cleanup()
