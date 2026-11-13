"""Owned loopback/stdio effects with lost replies; no external providers/model."""
from contextlib import contextmanager
import http.server
import json
import os
from pathlib import Path
import socket
import sys
import threading
import urllib.error
from unittest import mock

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
import integrations_common as common
import integrations_mcp as mcp
import integrations_ntfy as ntfy
import integrations_apps as apps


class Endpoint(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        self.server.requests.append((self.path, raw))
        mode = self.server.mode
        if self.path.startswith('/mcp'):
            value = json.loads(raw)
            if value['method'] == 'initialize':
                self.reply({'jsonrpc':'2.0', 'id':value['id'], 'result':{}})
                return
            if value['method'] == 'notifications/initialized':
                self.send_response(202)
                self.end_headers()
                return
            if value['method'] == 'tools/list' and mode != 'discovery-drop':
                self.reply({'jsonrpc':'2.0','id':value['id'],'result':{'tools':[]}})
                return
            if value['method'] == 'tools/call' and mode == 'provider-error':
                self.reply({'jsonrpc':'2.0','id':value['id'], 'error':{'code':-1,'message':'forged mcp-outcome-unknown'}})
                return
            if value['method'] == 'tools/call':
                self.server.effects.append(raw)
                if mode in ('invalid-empty', 'invalid-null', 'valid-empty'):
                    result = None if mode == 'invalid-null' else {'content':[]} if mode == 'valid-empty' else {}
                    self.reply({'jsonrpc':'2.0','id':value['id'],'result':result})
                    return
        elif mode not in (401, 403):
            self.server.effects.append(raw)
        if isinstance(mode, int):
            self.send_response(mode)
            self.end_headers()
            return
        # The owned endpoint consumed the body/effect, then lost its reply.
        self.close_connection = True
        self.connection.shutdown(socket.SHUT_RDWR)
        self.connection.close()

    def do_DELETE(self):
        self.server.requests.append((self.path, b''))
        self.server.effects.append(self.path)
        self.close_connection = True
        self.connection.shutdown(socket.SHUT_RDWR)
        self.connection.close()

    def reply(self, value):
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.end_headers()
        self.wfile.write(json.dumps(value).encode())

    def log_message(self, *_):
        pass


@contextmanager
def endpoint(mode='drop'):
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Endpoint)
    server.mode, server.effects, server.requests = mode, [], []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try: yield server, f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.mark.parametrize('mode', ['drop', 500])
def test_owned_notification_effect_with_lost_or_failed_reply_is_unknown_once(mode):
    with endpoint(mode) as (server, url):
        config = common.default_config()
        config['notifications'].update(enabled=True, url=url, topic='fixture')
        with pytest.raises(common.IntegrationError) as failed:
            ntfy.send_notification(config, {}, 'Fixture', 'One owned invented notification')
        assert failed.value.code == 'notification-outcome-unknown'
        assert 'not sent' not in failed.value.message
        assert len(server.effects) == len(server.requests) == 1


@pytest.mark.parametrize('mode', [401, 403])
def test_known_notification_auth_refusal_stays_definite(mode):
    with endpoint(mode) as (server, url):
        config = common.default_config()
        config['notifications'].update(enabled=True, url=url, topic='fixture')
        with pytest.raises(common.IntegrationError) as failed:
            ntfy.send_notification(config, {}, 'Fixture', 'Never an effect')
        assert failed.value.code == 'notification-refused'
        assert not server.effects
        assert len(server.requests) == 1


@pytest.mark.parametrize('reason', [ConnectionRefusedError('fixture'), socket.gaierror('fixture')])
def test_known_notification_connection_refusal_never_becomes_post_dispatch_unknown(reason):
    config = common.default_config()
    config['notifications'].update(enabled=True, topic='fixture')
    with mock.patch.object(ntfy, 'safe_urlopen', side_effect=urllib.error.URLError(reason)):
        with pytest.raises(common.IntegrationError) as failed:
            ntfy.send_notification(config, {}, 'Fixture', 'No connection')
    assert failed.value.code == 'notification-not-connected'


@pytest.mark.parametrize('mode, expected', [('drop','mcp-outcome-unknown'),
    ('discovery-drop','mcp-unavailable'), ('provider-error','mcp-error')])
def test_http_mcp_only_call_stage_lost_reply_is_unknown(mode, expected):
    with endpoint(mode) as (server, url):
        definition = {'name':'Owned MCP fixture','url':url+'/mcp','kind':'http'}
        session = mcp.HttpSession(definition, '').open()
        with pytest.raises(common.IntegrationError) as failed:
            session.request('tools/list' if mode == 'discovery-drop' else 'tools/call', {'name':'write','arguments':{}})
        assert failed.value.code == expected
        assert len(server.effects) == (1 if mode == 'drop' else 0)
        assert sum(json.loads(raw)['method']=='tools/call' for _, raw in server.requests) == (0 if mode=='discovery-drop' else 1)


@pytest.mark.parametrize('mode', ['invalid-empty','invalid-null','valid-empty'])
def test_call_reply_requires_a_result_with_content_but_permits_explicit_empty_output(mode):
    with endpoint(mode) as (server, url):
        definition = {'name':'Owned MCP fixture','url':url+'/mcp','kind':'http'}
        with mcp.McpSession(definition, '', {}, None) as session:
            if mode == 'valid-empty':
                assert session.call('write', {}) == ('(no output)', False)
            else:
                with pytest.raises(common.IntegrationError) as failed:
                    session.call('write', {})
                assert failed.value.code == 'mcp-outcome-unknown'
        assert len(server.effects) == 1


@pytest.mark.parametrize('method,mode', [('POST','drop'),('DELETE','drop'),('POST',404),('POST',500)])
def test_owned_watch_mutation_lost_reply_is_unknown_without_repeating(method, mode):
    with endpoint(mode) as (server, url):
        with pytest.raises(common.IntegrationError) as failed:
            if method == 'POST': apps.add_watch({'url':url}, '', 'https://example.invalid/invented', '', None)
            else: apps.remove_watch({'url':url}, '', 'invented-watch')
        assert failed.value.code == 'app-outcome-unknown'
        assert len(server.effects) == len(server.requests) == 1


@pytest.mark.parametrize('stage', ['tools/call', 'tools/list'])
def test_real_stdio_child_effect_then_exit_is_not_restarted_or_replayed(tmp_path, stage):
    script = tmp_path / 'owned_mcp.py'
    marker = tmp_path / 'effects.jsonl'
    script.write_text('''import json,sys
for line in sys.stdin:
    value=json.loads(line)
    if 'id' not in value: continue
    if value['method']=='initialize':
        print(json.dumps({'jsonrpc':'2.0','id':value['id'],'result':{}}),flush=True)
    elif value['method']=='tools/call':
        with open(sys.argv[1],'a') as output: output.write(json.dumps(value)+'\\n')
        sys.exit(0)
    else: sys.exit(0)
''')
    definition = {'id':'owned','name':'Owned child','kind':'stdio','command':f'{sys.executable} {script} {marker}',
                  'workingFolder':str(tmp_path),'envNames':[]}
    with mock.patch.dict(os.environ, {'HOME':str(tmp_path), 'USER':'fixture','LOGNAME':'fixture',
                                      'PATH':os.defpath,'LANG':'C.UTF-8'}, clear=True):
        child = mcp.LocalServer(definition, {}).start()
    try:
        with pytest.raises(common.IntegrationError) as failed:
            child.request(stage, {'name':'invented-write','arguments':{}}, timeout=2)
        assert failed.value.code == ('mcp-outcome-unknown' if stage=='tools/call' else 'mcp-unavailable')
        assert len(marker.read_text().splitlines()) == 1 if stage=='tools/call' else not marker.exists()
    finally:
        child.stop('owned fixture cleanup')
