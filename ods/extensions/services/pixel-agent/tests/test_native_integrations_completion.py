"""Real native cancellation/admission with a private invented integration backend.

The SSE responder is deterministic fixture data, never an inference service.
Only the integration socket connector changes; factories, native hooks and SDK
dispatch/cancellation are the production implementations. Installed SDK is read-only.
"""
import hashlib
import http.server
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import socketserver
import threading
import time
import unittest
import urllib.error
import urllib.request

if __package__:
    from . import test_connection_plugin_runtime as fixture
else:
    import test_connection_plugin_runtime as fixture

ROOT = Path(__file__).resolve().parents[4]
WRAPPER = r'''
import original from '../real-portal/index.js';
import {createIntegrationTools,submitIntegration} from '../real-portal/integrations.mjs';
import {withPiToolErrorContract} from '../real-portal/pi-tool-result.mjs';
import fs from 'node:fs';
import net from 'node:net';
const fixture=JSON.parse(fs.readFileSync(new URL('./fixture.json',import.meta.url),'utf8'));
const actual=new Map(createIntegrationTools({submit:(payload,options)=>submitIntegration(payload,{
  ...options,connect:()=>net.createConnection({path:fixture.socket}),
})}).map(tool=>[tool.name,tool]));
const wrap=tool=>{
  if(Array.isArray(tool))return tool.map(wrap);
  if(!actual.has(tool?.name))return tool;
  const integration=actual.get(tool.name);
  return {...tool,execute:withPiToolErrorContract({...integration,execute:async(id,args,signal,...rest)=>{
    fs.writeFileSync(fixture.entered,JSON.stringify({signalPresent:!!signal}),{mode:0o600});
    signal?.addEventListener('abort',()=>fs.writeFileSync(fixture.aborted,'actual-native-abort',{mode:0o600}),{once:true});
    try{return await integration.execute(id,args,signal,...rest);}
    finally{fs.writeFileSync(fixture.settled,'actual-factory-settled',{mode:0o600});}
  }}).execute};
};
export default {...original,register(api){
  original.register({...api,registerTool(definition,options){
    api.registerTool(typeof definition==='function'?context=>wrap(definition(context)):wrap(definition),options);
  }});
  for(const phase of ['before_agent_run','before_tool_call','after_tool_call','tool_result_persist','agent_end'])
    api.on(phase,(event,context)=>{
      fs.appendFileSync(fixture.events,JSON.stringify({phase,tool:event?.toolName??event?.message?.toolName??context?.toolName??null,
        callId:event?.toolCallId??event?.message?.toolCallId??context?.toolCallId??null,runId:context?.runId??event?.runId??null,
        agentId:context?.agentId??null,sessionId:context?.sessionId??null,sessionKey:context?.sessionKey??null,
        resultError:event?.result?.isError===true,piError:event?.result?.details?.ok===false,
        childError:event?.result?.details?.result?.isError===true,
        persistedError:event?.message?.isError===true,
        toolId:event?.result?.details?.tool?.id??null,
        failed:!!event?.error})+'\n',{mode:0o600});
    });
}};
'''


@unittest.skipUnless(fixture.PACKAGE and os.name == 'posix', 'Requires pinned native OpenClaw')
class NativeIntegrationCompletionTests(unittest.TestCase):
    def test_native_cancel_retains_admission_until_submitted_integration_settles(self):
        source_package = Path(fixture.PACKAGE)
        fixture.PluginRuntimeTests.setUpClass()
        world = fixture.PluginRuntimeTests()
        world.setUp()
        self.addCleanup(world.doCleanups)
        package = world.home / 'openclaw'
        shutil.copytree(source_package, package, ignore=shutil.ignore_patterns('node_modules'))
        if (source_package / 'node_modules').is_dir():
            (package / 'node_modules').symlink_to(source_package / 'node_modules', target_is_directory=True)
        (world.home / 'node_modules').mkdir()
        (world.home / 'node_modules/openclaw').symlink_to(package, target_is_directory=True)
        world.openclaw = str(package / 'openclaw.mjs')
        host = ROOT / 'extensions/services/pixel-agent/host'
        spec = importlib.util.spec_from_file_location('coffee_native_repair', host / 'openclaw_tool_recovery.py')
        repair = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(repair)
        source_hashes = {}
        for name, manifest_name in [('tools-invoke-shared-dvxhZAis.js', 'openclaw-direct-tool-completion.json'),
                                    ('tool-search-BInRpkE3.js', 'openclaw-image-envelope.json')]:
            original_path = source_package / 'dist' / name
            source_hashes[name] = hashlib.sha256(original_path.read_bytes()).hexdigest()
            recipe = json.loads((host / manifest_name).read_text())
            candidate = (package / 'dist' / name).read_text()
            digest = hashlib.sha256(candidate.encode()).hexdigest()
            previous = recipe['replacements'] if digest == recipe['patchedSha256'] else recipe.get('previousReplacements', {}).get(digest)
            if previous is not None:
                for before, after in reversed(previous):
                    self.assertEqual(candidate.count(after), 1)
                    candidate = candidate.replace(after, before)
            self.assertEqual(hashlib.sha256(candidate.encode()).hexdigest(), recipe['sourceSha256'])
            (package / 'dist' / name).write_text(candidate)
            recipe_path = host / manifest_name
            if name == repair.IMAGE_MODULE and os.environ.get('ODS_COFFEE_COMPLETION_PATCH') == '0':
                recipe['patchedSha256'] = '493b575a20d3e1dc6ab2f6b356b9acdd82c97c874258c140799946149d60fb60'
                recipe['replacements'] = recipe['previousReplacements'][recipe['patchedSha256']]
                recipe['previousReplacements'] = {}
                recipe_path = world.home / 'reviewed-before-image.json'
                recipe_path.write_text(json.dumps(recipe))
            changed = repair.repair(package, world.home / ('repair-' + name), module_name=name, manifest_path=recipe_path)
            self.assertEqual(changed['status'], 'changed')
        self.addCleanup(lambda: self.assertEqual({name: hashlib.sha256((source_package / 'dist' / name).read_bytes()).hexdigest()
                                                  for name in source_hashes}, source_hashes))
        world.env['OPENCLAW_SKIP_CHANNELS'] = '1'
        real = world.home / 'real-portal'
        shutil.copytree(ROOT / 'extensions/services/pixel-agent/plugin', real)
        plugin = world.home / 'portal-fixture'
        plugin.mkdir(mode=0o700)
        for directory in (real, plugin):
            for entry in [directory, *directory.rglob('*')]:
                entry.chmod(0o755 if entry.is_dir() else 0o644)
        shutil.copy2(real / 'package.json', plugin / 'package.json')
        shutil.copy2(real / 'openclaw.plugin.json', plugin / 'openclaw.plugin.json')
        # The cloned manifest's extension entry remains index.js, static source.
        (plugin / 'index.js').write_text(WRAPPER)
        entered, aborted, settled = (world.home / name for name in ('factory-entered', 'native-aborted', 'factory-settled'))
        events = world.home / 'native-hooks.jsonl'
        socket_path = world.home / 'integrations.sock'
        (plugin / 'fixture.json').write_text(json.dumps({'socket': str(socket_path), 'entered': str(entered),
                                                        'aborted': str(aborted), 'settled': str(settled), 'events': str(events)}))
        admitted, release, backend_done = threading.Event(), threading.Event(), threading.Event()
        fail_backend = threading.Event()
        submissions = []

        class Integration(socketserver.StreamRequestHandler):
            def handle(handler):
                line = handler.rfile.readline(65537)
                submissions.append(json.loads(line))
                admitted.set()
                release.wait(timeout=15)
                try:
                    handler.wfile.write((json.dumps({'ok': True, 'server': 'invented', 'tool': 'fixture',
                        'text': 'invented failure' if fail_backend.is_set() else 'invented completion',
                        'isError': fail_backend.is_set()}) + '\n').encode())
                except BrokenPipeError:
                    pass
                finally:
                    backend_done.set()

        backend = socketserver.ThreadingUnixStreamServer(str(socket_path), Integration)
        backend.daemon_threads = True
        thread = threading.Thread(target=backend.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(backend.server_close)
        self.addCleanup(backend.shutdown)
        self.addCleanup(release.set)
        calls = []

        class Provider(http.server.BaseHTTPRequestHandler):
            def do_POST(handler):
                payload = json.loads(handler.rfile.read(int(handler.headers['Content-Length'])))
                calls.append(payload)
                if not any(message.get('role') == 'tool' for message in payload.get('messages', [])):
                    args = {'id': 'openclaw:pixel-ods:pixel_ods_mcp', 'args': {'action': 'call',
                        'server': 'invented', 'tool': 'fixture', 'arguments': {}}}
                    delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'coffee-fixture-call',
                        'type': 'function', 'function': {'name': 'tool_call', 'arguments': json.dumps(args)}}]}
                    finish = 'tool_calls'
                else:
                    delta, finish = {'role': 'assistant', 'content': 'Invented completion.'}, 'stop'
                chunks = [{'id': 'fixture', 'object': 'chat.completion.chunk',
                           'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
                          {'id': 'fixture', 'object': 'chat.completion.chunk',
                           'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}],
                           'usage': {'prompt_tokens': 1000, 'completion_tokens': 20, 'total_tokens': 1020}}]
                handler.send_response(200)
                handler.send_header('Content-Type', 'text/event-stream')
                handler.end_headers()
                for chunk in chunks:
                    handler.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
                handler.wfile.write(b'data: [DONE]\n\n')

            def log_message(self, *_):
                pass

        provider = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Provider)
        threading.Thread(target=provider.serve_forever, daemon=True).start()
        self.addCleanup(provider.server_close)
        self.addCleanup(provider.shutdown)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        token = secrets.token_hex(24)
        world.write_config({'update': {'checkOnStart': False},
            'gateway': {'mode': 'local', 'bind': 'loopback', 'port': port,
                'auth': {'mode': 'token', 'token': token}, 'http': {'endpoints': {'chatCompletions': {'enabled': True}}}},
            'plugins': {'allow': ['pixel-ods'], 'load': {'paths': [str(plugin)]},
                'entries': {'pixel-ods': {'enabled': True, 'hooks': {'allowConversationAccess': True}, 'config': {}}}},
            'agents': {'defaults': {'heartbeat': {'every': '0m'}, 'skipBootstrap': True,
                'model': {'primary': 'fixture/model'}, 'contextTokens': 32768, 'sandbox': {'mode': 'off'}},
                'list': [{'id': 'pixel', 'default': True, 'workspace': str(world.home / 'workspace'),
                          'tools': {'exec': {'host': 'gateway', 'security': 'full', 'ask': 'off'}}}]},
            'tools': {'allow': ['pixel_ods_mcp', 'tool_search', 'tool_describe', 'tool_call'],
                'toolSearch': {'enabled': True, 'mode': 'tools'}},
            'models': {'mode': 'replace', 'providers': {'fixture': {'api': 'openai-completions',
                'baseUrl': f'http://127.0.0.1:{provider.server_port}/v1', 'apiKey': 'invented-only',
                'models': [{'id': 'model', 'name': 'Invented fixture', 'contextWindow': 32768,
                    'maxTokens': 2048, 'reasoning': False, 'input': ['text']}]}}}})
        world.cli('plugins', 'registry', '--refresh', '--json')
        loaded = world.cli('plugins', 'inspect', 'pixel-ods', '--runtime', '--json')['plugin']
        self.assertEqual(loaded['status'], 'loaded', loaded.get('error'))
        world.start_gateway(port, 'coffee-native')

        def request(body=None, path='/pixel-ods/access-runtime', timeout=5):
            req = urllib.request.Request(f'http://127.0.0.1:{port}{path}',
                data=json.dumps(body).encode() if body else None,
                headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    return response.status, json.load(response)
            except urllib.error.HTTPError as error:
                return error.code, json.load(error)

        self.assertEqual(request()[1]['active'], 0)
        chat = socket.create_connection(('127.0.0.1', port), timeout=3)
        self.addCleanup(chat.close)
        user = 'ods-' + hashlib.sha256(b'qa-coffee-native-cancel').hexdigest()
        body = json.dumps({'model': 'openclaw:pixel', 'stream': True, 'user': user,
                          'messages': [{'role': 'user', 'content': 'Call the invented fixture MCP tool and wait for completion.'}]}).encode()
        headers = (f'POST /v1/chat/completions HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n'
                   f'Authorization: Bearer {token}\r\nContent-Type: application/json\r\n'
                   f'Content-Length: {len(body)}\r\nConnection: close\r\n\r\n').encode()
        chat.sendall(headers + body)
        try:
            self.assertTrue(admitted.wait(timeout=20), (world.home / 'coffee-native.log').read_text()[-4000:])
            self.assertEqual(submissions[0]['action'], 'mcp-call')
            self.assertTrue(json.loads(entered.read_text())['signalPresent'], 'Native executor must pass its real abort signal')
            chat.shutdown(socket.SHUT_RDWR)
            chat.close()
            deadline = time.monotonic() + 4
            while not aborted.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(aborted.exists(), 'Actual HTTP cancellation did not reach the integration tool; do not substitute a fixture abort')
            snapshot = request()[1]
            self.assertTrue(snapshot['available'], snapshot)
            self.assertGreaterEqual(snapshot['activity']['tools'], 1, snapshot)
            self.assertEqual(snapshot['phase'], 'busy', snapshot)
            self.assertFalse(settled.exists(), 'Real tool promise settled before the invented backend')
            lease = secrets.token_hex(32)
            self.assertEqual(request({'operation': 'acquire', 'token': lease, 'revision': snapshot['revision']})[0], 409)
        finally:
            release.set()
        self.assertTrue(backend_done.wait(timeout=3))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            snapshot = request()[1]
            if snapshot['active'] == 0 and settled.exists():
                break
            time.sleep(.02)
        self.assertTrue(settled.exists())
        trace = [json.loads(line) for line in events.read_text().splitlines()]
        self.assertEqual((snapshot['phase'], snapshot['active']), ('idle', 0),
                         {'snapshot': snapshot, 'factorySettled': True, 'backendDone': backend_done.is_set(), 'hooks': trace})
        before = [item for item in trace if item['phase'] == 'before_tool_call' and item['tool'] == 'tool_call']
        after = [item for item in trace if item['phase'] == 'after_tool_call' and item['tool'] == 'tool_call']
        self.assertEqual((len(before), len(after)), (1, 1), trace)
        for key in ('callId', 'runId', 'agentId', 'sessionId', 'sessionKey'):
            self.assertEqual(after[0][key], before[0][key], {'key': key, 'hooks': trace})
        code, held = request({'operation': 'acquire', 'token': lease, 'revision': snapshot['revision']})
        self.assertEqual(code, 200, held)
        self.assertEqual(request({'operation': 'release', 'token': lease, 'revision': held['revision']})[0], 200)
        self.assertEqual(len(submissions), 1)
        # Normal/error controls use the same actual agent ToolSearch surface;
        # HTTP direct tools do not expose tool_call and are tested separately.
        for mode in ('normal', 'failure'):
            if mode == 'failure':
                fail_backend.set()
            user = 'ods-' + hashlib.sha256(('qa-coffee-native-' + mode).encode()).hexdigest()
            code, _ = request({'model': 'openclaw:pixel', 'stream': False, 'user': user,
                'messages': [{'role': 'user', 'content': 'Call the invented fixture MCP tool and wait for completion.'}]},
                '/v1/chat/completions', timeout=20)
            self.assertEqual(code, 200)
            self.assertEqual(request()[1]['active'], 0)
            trace = [json.loads(line) for line in events.read_text().splitlines()]
            terminal = [item for item in trace if item['tool'] == 'tool_call' and item['phase'] == 'after_tool_call'][-1]
            # Native Pi emits a sanitized AgentToolResult in this hook; its
            # authoritative error is event.error plus the persisted isError,
            # not preservation of the raw execute-return isError property.
            self.assertEqual(terminal['failed'], mode == 'failure', terminal)
            self.assertEqual(terminal['piError'], mode == 'failure', terminal)
            self.assertEqual(terminal['childError'], mode == 'failure', terminal)
            self.assertEqual(terminal['toolId'], 'openclaw:pixel-ods:pixel_ods_mcp', terminal)
            persisted = [item for item in trace if item['phase'] == 'tool_result_persist'
                         and item['tool'] == 'tool_call' and item['callId'] == terminal['callId']
                         and item['sessionKey'] == terminal['sessionKey']]
            self.assertEqual(len(persisted), 1, trace)
            self.assertEqual(persisted[0]['persistedError'], mode == 'failure', persisted)
        self.assertEqual(len(submissions), 3)
        trace = [json.loads(line) for line in events.read_text().splitlines()]
        outer = [item for item in trace if item['tool'] == 'tool_call']
        calls_before = [item for item in outer if item['phase'] == 'before_tool_call']
        calls_after = [item for item in outer if item['phase'] == 'after_tool_call']
        self.assertEqual((len(calls_before), len(calls_after)), (3, 3), outer)
        for item in calls_before:
            self.assertEqual(sum(other['callId'] == item['callId'] and other['runId'] == item['runId']
                                 for other in calls_after), 1, outer)


if __name__ == '__main__':
    unittest.main()
