"""Pinned native MCP filtering through Pixel and an actual embedded agent turn.

The provider is a controlled loopback SSE fixture, not model inference. It
requests an unrelated server's read and an excluded write, then the permitted
read. Exact tool grants must not authorize every configured MCP server. No
owner configuration, credentials, network app or Docker is used.
"""
import http.server
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch
import urllib.request

import test_connection_plugin_runtime as fixture

ROOT = Path(__file__).resolve().parents[4]
SERVER = r'''
import json,sys
from pathlib import Path
log=Path(sys.argv[1]);fact=sys.argv[2]
for line in sys.stdin:
    value=json.loads(line);method=value.get('method');identity=value.get('id')
    if identity is None:continue
    if method=='initialize':
        result={'protocolVersion':value['params']['protocolVersion'],'capabilities':{'tools':{}},'serverInfo':{'name':'ods-qa-project','version':'1'}}
    elif method=='tools/list':
        result={'tools':[{'name':name,'description':description,'inputSchema':{'type':'object','properties':{},'additionalProperties':False}}
            for name,description in [('project_status','Read Project Cedar status.'),('project_delete','Delete Project Cedar.')]]}
    elif method=='tools/call':
        name=value['params']['name']
        with log.open('a') as handle:handle.write(json.dumps({'name':name})+'\n')
        result={'content':[{'type':'text','text':fact if name=='project_status' else 'EXCLUDED WRITE REACHED SERVER'}]}
    elif method=='ping':result={}
    else:result={}
    print(json.dumps({'jsonrpc':'2.0','id':identity,'result':result}),flush=True)
'''


@unittest.skipUnless(fixture.PACKAGE and sys.platform == 'linux',
                     'Set OPENCLAW_PACKAGE_DIR for the pinned Linux/WSL native MCP check')
class NativeMcpRuntimeTests(unittest.TestCase):
    def test_exact_grant_isolates_servers_and_preserves_the_allowed_read(self):
        fixture.PluginRuntimeTests.setUpClass()
        world = fixture.PluginRuntimeTests()
        world.setUp()
        self.addCleanup(world.doCleanups)
        fact = 'Project Cedar: Friday 10:15. ' + secrets.token_hex(12)
        calls = world.home / 'mcp-calls.jsonl'
        unrelated_calls = world.home / 'unrelated-mcp-calls.jsonl'
        server = world.home / 'project_mcp.py'
        server.write_text(SERVER)
        server.chmod(0o600)
        requests, failures = [], []
        tool_ids = ('qa-unrelated__project_status', 'qa-project__project_delete', 'qa-project__project_status')

        class Provider(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def do_POST(handler):
                length = int(handler.headers['Content-Length'])
                if length > 2 * 1024 * 1024:
                    handler.send_error(413)
                    return
                request = json.loads(handler.rfile.read(length))
                requests.append(request)
                step = len(requests)
                choices = [('tool_search', {'query': 'project_status', 'limit': 5}),
                           ('tool_call', {'id': tool_ids[0], 'args': {}}),
                           ('tool_call', {'id': tool_ids[1], 'args': {}}),
                           ('tool_call', {'id': tool_ids[2], 'args': {}})]
                if step <= len(choices):
                    name, args = choices[step - 1]
                    delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'call-' + str(step),
                             'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}
                    finish = 'tool_calls'
                else:
                    tool_results = [item.get('content') for item in request['messages'] if item.get('role') == 'tool']
                    if step != 5 or fact not in json.dumps(tool_results):
                        failures.append('Allowed MCP read did not reach the provider in a tool result')
                    delta, finish = {'role': 'assistant', 'content': fact}, 'stop'
                chunks = [{'id': 'native-mcp-qa', 'object': 'chat.completion.chunk',
                           'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
                          {'id': 'native-mcp-qa', 'object': 'chat.completion.chunk',
                           'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}],
                           'usage': {'prompt_tokens': 200, 'completion_tokens': 30, 'total_tokens': 230}}]
                raw = (''.join('data: ' + json.dumps(item) + '\n\n' for item in chunks) + 'data: [DONE]\n\n').encode()
                handler.send_response(200)
                handler.send_header('Content-Type', 'text/event-stream')
                handler.send_header('Content-Length', str(len(raw)))
                handler.end_headers()
                handler.wfile.write(raw)

            def log_message(handler, *_args):
                pass

        provider = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Provider)
        provider.daemon_threads = True
        thread = threading.Thread(target=provider.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(provider.server_close)
        self.addCleanup(provider.shutdown)
        token = secrets.token_hex(24)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        plugin = world.home / 'portal-plugin'
        shutil.copytree(ROOT / 'extensions/services/pixel-agent/plugin', plugin)
        # A WSL checkout on NTFS can present every source file as mode 0777.
        # The disposable plugin must have the same safe modes as an installation.
        plugin.chmod(0o755)
        for entry in plugin.rglob('*'):
            if not entry.is_symlink():
                entry.chmod(0o755 if entry.is_dir() else 0o644)
        config = {
            'gateway': {'mode': 'local', 'bind': 'loopback', 'port': port, 'auth': {'mode': 'token', 'token': token},
                        'http': {'endpoints': {'chatCompletions': {'enabled': True}}}},
            'logging': {'file': str(world.home / 'gateway.log')}, 'session': {},
            'plugins': {'allow': ['pixel-ods', 'llama-cpp'], 'load': {'paths': [str(plugin)]},
                        'entries': {'pixel-ods': {'enabled': True, 'config': {}}}},
            'models': {'providers': {'ods-local': {'api': 'openai-completions', 'apiKey': 'local-no-auth',
                       'baseUrl': 'http://127.0.0.1:11434/v1',
                       'models': [{'id': 'Qwen3.5-2B', 'name': 'ODS Local Qwen3.5-2B', 'contextWindow': 16384,
                                   'maxTokens': 2048, 'reasoning': False, 'input': ['text']}]}}},
            'agents': {'defaults': {'sandbox': {'mode': 'off'}}, 'list': [{'id': 'pixel', 'default': True,
                       'workspace': str(world.home / 'workspace'), 'model': 'ods-local/Qwen3.5-2B'}]},
            'tools': {'profile': 'coding', 'alsoAllow': [], 'sandbox': {'tools': {'allow': []}}},
        }
        candidate = world.home / 'candidate.json'
        candidate.write_text(json.dumps(config))
        candidate.chmod(0o600)
        staged = subprocess.run([sys.executable, str(ROOT / 'installers/lib/pixel-runtime-budget.py'),
            str(candidate), '3004', '', str(world.state)], env={**os.environ, **world.env},
            capture_output=True, text=True, check=True).stdout.strip()
        config = json.loads((candidate if staged == 'unchanged' else Path(staged)).read_bytes())
        config['models']['providers']['ods-local']['baseUrl'] = 'http://127.0.0.1:%d/v1' % provider.server_port
        # Fixture-only grant: the native registry is not yet exposed in Portal's
        # supported app catalog. This test does not authorize arbitrary servers.
        config['agents']['list'][0]['tools']['allow'] = [tool_ids[2]]
        config['tools']['sandbox']['tools']['allow'].append(tool_ids[2])
        config['mcp'] = {'servers': {'qa-project': {'enabled': True, 'transport': 'stdio',
                         'command': sys.executable, 'args': ['-I', str(server), str(calls), fact],
                         'toolFilter': {'include': ['project_status']}},
                         'qa-unrelated': {'enabled': True, 'transport': 'stdio',
                         'command': sys.executable,
                         'args': ['-I', str(server), str(unrelated_calls), 'UNGRANTED ACCOUNT DATA']}}}
        world.write_config(config)
        # Reconfigure with Pixel's real renderer before exercising the native
        # server. A catalog/probe-only test would miss losing the registry or
        # its effective tool restrictions during an ODS update.
        spec = importlib.util.spec_from_file_location('native_mcp_renderer_fixture',
            ROOT / 'vendor/pixel/tests/test_native_search.py')
        rendering = importlib.util.module_from_spec(spec)
        with patch.object(sys, 'dont_write_bytecode', True):
            spec.loader.exec_module(rendering)
        rendering.NativeSearchTests.setUpClass()
        self.addCleanup(rendering.NativeSearchTests.tearDownClass)
        render_case = rendering.NativeSearchTests()
        render_case.setUp()
        self.addCleanup(render_case.doCleanups)
        digest = subprocess.check_output(['node', str(ROOT / 'vendor/pixel/scripts/hash-gateway-extension.mjs'),
                                          str(plugin)], text=True).strip()
        tools = json.loads((plugin / 'openclaw.plugin.json').read_text())['contracts']['tools']
        render_case.answers.update(openclawHome=str(world.state), workspace=str(world.home / 'workspace'),
            modelProvider='ods-local', modelId='Qwen3.5-2B', modelName='ODS Local Qwen3.5-2B',
            modelBaseUrl='http://127.0.0.1:11434/v1', modelContextWindow=16384,
            modelMaxTokens=2048, modelReasoning=False, webSearchProvider='searxng',
            webLimbEnabled=False, emailLimbEnabled=False, calendarLimbEnabled=False,
            gatewayExtensions=[{'id': 'pixel-ods', 'path': str(plugin), 'sha256': digest, 'tools': tools}])
        render_case.configure()
        environment = render_case.configured_env()
        environment['PIXEL_GATEWAY_TOKEN'] = token
        rendered = render_case.render(environment)
        self.assertEqual(rendered['mcp'], config['mcp'])
        self.assertIn(tool_ids[2], rendered['tools']['sandbox']['tools']['allow'])
        self.assertNotIn('bundle-mcp', rendered['tools']['sandbox']['tools']['allow'])
        candidate.write_text(json.dumps(rendered))
        staged = subprocess.run([sys.executable, str(ROOT / 'installers/lib/pixel-runtime-budget.py'),
            str(candidate), '3004', '', str(world.state)], env={**os.environ, **world.env},
            capture_output=True, text=True, check=True).stdout.strip()
        config = json.loads((candidate if staged == 'unchanged' else Path(staged)).read_bytes())
        config['models']['providers']['ods-local']['baseUrl'] = 'http://127.0.0.1:%d/v1' % provider.server_port
        config['gateway']['port'] = port
        # Keep inference and execution within the disposable fixture. This
        # qualifies native MCP policy/filtering, not Docker sandbox execution.
        config['agents']['defaults']['sandbox']['mode'] = 'off'
        config['agents']['list'][0]['sandbox'] = {'mode': 'off'}
        config['agents']['list'][0]['tools'].setdefault('exec', {})['host'] = 'gateway'
        config['agents']['defaults']['sandbox']['docker']['containerPrefix'] = 'ods-mcp-qa-' + secrets.token_hex(8) + '-'
        world.write_config(config)
        validation = subprocess.run([world.openclaw, 'config', 'validate'], cwd=world.home,
                                    env=world.env, capture_output=True, text=True)
        self.assertEqual(validation.returncode, 0, validation.stderr[-3000:])
        world.cli('plugins', 'registry', '--refresh', '--json')
        primary = world.cli('plugins', 'inspect', 'pixel-ods', '--runtime', '--json')['plugin']
        self.assertEqual(primary['status'], 'loaded', 'Pixel must register before its MCP turn can qualify')
        self.assertIs(primary['imported'], True)
        self.assertEqual(primary['rootDir'], str(plugin.resolve()))
        probe = world.cli('mcp', 'probe', 'qa-project', '--json')
        self.assertEqual(probe['tools'], ['qa-project__project_status'])
        self.assertEqual(probe['servers']['qa-project']['filteredTools'], 1)
        self.assertEqual(probe['diagnostics'], [])
        world.start_gateway(port, 'native-mcp')
        request = urllib.request.Request('http://127.0.0.1:%d/v1/chat/completions' % port,
            # Portal supplies this canonical conversation namespace. An
            # arbitrary native user label correctly fails image custody.
            data=json.dumps({'model': 'openclaw:pixel', 'user': 'ods-' + secrets.token_hex(32), 'stream': False,
                             'messages': [{'role': 'user', 'content': 'Read Project Cedar using its connected tools.'}]}).encode(),
            headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=90) as response:
            result = json.load(response)
        self.assertEqual(failures, [])
        self.assertEqual(len(requests), 5, json.dumps(result))
        for step, tool_id in ((2, tool_ids[0]), (3, tool_ids[1])):
            rejected = [item['content'] for item in requests[step]['messages'] if item.get('role') == 'tool']
            self.assertIn('Unknown tool id', json.dumps(rejected))
            self.assertIn(tool_id, json.dumps(rejected))
        self.assertNotIn(tool_ids[0], json.dumps(requests[0]['tools']))
        discovered = json.dumps([item['content'] for item in requests[1]['messages'] if item.get('role') == 'tool'])
        self.assertNotIn(tool_ids[0], discovered)
        self.assertNotIn(tool_ids[1], discovered)
        self.assertFalse(unrelated_calls.exists(), 'An ungranted MCP server must receive no tool calls')
        self.assertEqual([json.loads(line)['name'] for line in calls.read_text().splitlines()], ['project_status'])
        self.assertEqual(result['choices'][0]['message']['content'], fact)


if __name__ == '__main__':
    unittest.main()
