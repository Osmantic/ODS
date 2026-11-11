"""Pinned native hooks/factories with invented SSE and an owned Unix backend.

No model, installed ODS, provider account, Root gateway or GPU is used. The
private SDK is read-only; only the integration socket endpoint is redirected.
"""
import hashlib
import http.server
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import socketserver
import threading
import unittest
import urllib.request

if __package__:
    from . import test_connection_plugin_runtime as fixture
else:
    import test_connection_plugin_runtime as fixture


ROOT = Path(__file__).resolve().parents[4]
WRAPPER = r'''
import original from '../real-portal/index.js';
import fs from 'node:fs';
import net from 'node:net';
const fixture=JSON.parse(fs.readFileSync(new URL('./fixture.json',import.meta.url),'utf8'));
const connect=net.createConnection;
net.createConnection=function(options,...args){
  return connect.call(this,options?.path==='/run/ods-pixel-integrations/control.sock'
    ? {...options,path:fixture.socket}:options,...args);
};
const record=value=>fs.appendFileSync(fixture.events,JSON.stringify(value)+'\n',{mode:0o600});
const wrap=tool=>{
  if(Array.isArray(tool))return tool.map(wrap);
  if(!['pixel_ods_mcp','pixel_ods_notify'].includes(tool?.name))return tool;
  // Keep the actual production factory closure, admission and abort signal.
  return {...tool,execute:async(...args)=>{
    record({phase:'execute',name:tool.name,id:args[0]});
    const result=await tool.execute(...args);
    if(result?.details?.status==='outcome-unknown'){
      const denied=await tool.execute(args[0]+':direct-fixture-repeat',
        {action:'call',server:'invented',tool:'write',arguments:{changed:true}},args[2]);
      record({phase:'direct-repeat',code:denied?.details?.code});
    }
    return result;
  }};
};
export default {...original,register(api){
  original.register({...api,registerTool(definition,options){
    api.registerTool(typeof definition==='function'?context=>wrap(definition(context)):wrap(definition),options);
  }});
  for(const phase of ['before_prompt_build','before_tool_call','after_tool_call','agent_end'])
    api.on(phase,(event,context)=>record({phase,tool:event?.toolName??context?.toolName,
      id:event?.toolCallId??context?.toolCallId,runId:context?.runId??event?.runId,
      sessionId:context?.sessionId,sessionKey:context?.sessionKey,trigger:context?.trigger,error:!!event?.error}));
}};
'''


@unittest.skipUnless(fixture.PACKAGE and os.name == 'posix', 'Requires the private pinned SDK fixture')
class NativeOutcomeTests(unittest.TestCase):
    def test_native_unknown_blocks_repeats_but_preserves_reads_and_fresh_owner_eligibility(self):
        fixture.PluginRuntimeTests.setUpClass()
        world = fixture.PluginRuntimeTests()
        world.setUp()
        self.addCleanup(world.doCleanups)
        package = Path(fixture.PACKAGE)
        (world.home / 'node_modules').mkdir()
        (world.home / 'node_modules/openclaw').symlink_to(package, target_is_directory=True)
        world.env['OPENCLAW_SKIP_CHANNELS'] = '1'
        real = world.home / 'real-portal'
        shutil.copytree(ROOT / 'extensions/services/pixel-agent/plugin', real)
        plugin = world.home / 'outcome-fixture'
        plugin.mkdir(mode=0o755)
        for path in [real, *real.rglob('*')]: path.chmod(0o755 if path.is_dir() else 0o644)
        for name in ('package.json','openclaw.plugin.json'): shutil.copy2(real / name, plugin / name)
        (plugin / 'index.js').write_text(WRAPPER)
        events, socket_path = world.home / 'events.jsonl', world.home / 'owned.sock'
        (plugin / 'fixture.json').write_text(json.dumps({'socket':str(socket_path),'events':str(events)}))
        submissions, effects, reads, memory = [], [], [], []
        backend_state = {'unknown':True,'pending':False}

        class Backend(socketserver.StreamRequestHandler):
            def handle(handler):
                value = json.loads(handler.rfile.readline(65537))
                submissions.append(value)
                if value.get('action') == 'digest-plan':
                    reply = {'ok':True,'plan':{'enabled':False}}
                elif value.get('action') in ('memory-context','memory-answer'):
                    # The owner's memory around each chat turn (integration-memory.mjs), not a tool's read.
                    memory.append(value)
                    reply = {'ok':True,'text':'','enabled':False,'quiet':False}
                elif value.get('action') == 'mcp-call' and backend_state['pending']:
                    reply = {'ok':True,'status':'approval-required','approvalId':'owned_fixture_pending',
                             'summary':'Owned invented action needs approval'}
                elif value.get('action') == 'mcp-call' and value.get('server') == 'invented':
                    effects.append(value)
                    reply = ({'ok':False,'error':'mcp-outcome-unknown','message':'Invented lost reply after effect'}
                             if backend_state['unknown'] else {'ok':True,'text':'Invented successful call'})
                else:
                    reads.append(value)
                    reply = {'ok':True,'text':'Invented document read'}
                handler.wfile.write((json.dumps(reply)+'\n').encode())

        backend = socketserver.ThreadingUnixStreamServer(str(socket_path), Backend)
        threading.Thread(target=backend.serve_forever, daemon=True).start()
        self.addCleanup(backend.server_close)
        self.addCleanup(backend.shutdown)
        plans, provider_calls = [], []

        class InventedSSE(http.server.BaseHTTPRequestHandler):
            def do_GET(handler):
                handler.send_response(200)
                handler.end_headers()
                handler.wfile.write(b'{"ready":false}')

            def do_POST(handler):
                payload = json.loads(handler.rfile.read(int(handler.headers['Content-Length'])))
                provider_calls.append(payload)
                step = plans.pop(0) if plans else {'text':'Invented terminal answer.'}
                if 'args' in step:
                    delta = {'role':'assistant','tool_calls':[{'index':0,'id':step['id'],'type':'function',
                        'function':{'name':'tool_call','arguments':json.dumps({'id':'openclaw:pixel-ods:pixel_ods_mcp','args':step['args']})}}]}
                    finish = 'tool_calls'
                else:
                    delta, finish = {'role':'assistant','content':step['text']}, 'stop'
                handler.send_response(200)
                handler.send_header('Content-Type','text/event-stream')
                handler.end_headers()
                for choice in ({'index':0,'delta':delta,'finish_reason':None}, {'index':0,'delta':{},'finish_reason':finish}):
                    handler.wfile.write(('data: '+json.dumps({'id':'invented','object':'chat.completion.chunk','choices':[choice]})+'\n\n').encode())
                handler.wfile.write(b'data: [DONE]\n\n')

            def log_message(self, *_):
                pass

        responder = http.server.ThreadingHTTPServer(('127.0.0.1',0), InventedSSE)
        threading.Thread(target=responder.serve_forever, daemon=True).start()
        self.addCleanup(responder.server_close)
        self.addCleanup(responder.shutdown)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',0))
            port = probe.getsockname()[1]
        token = secrets.token_hex(24)
        world.write_config({'update':{'checkOnStart':False},
            'gateway':{'mode':'local','bind':'loopback','port':port,'auth':{'mode':'token','token':token},
                'http':{'endpoints':{'chatCompletions':{'enabled':True}}}},
            'plugins':{'allow':['pixel-ods'],'load':{'paths':[str(plugin)]},'entries':{'pixel-ods':{
                'enabled':True,'hooks':{'allowConversationAccess':True},'config':{'perplexicaPort':responder.server_port}}}},
            'agents':{'defaults':{'heartbeat':{'every':'0m'},'skipBootstrap':True,'model':{'primary':'fixture/model'},
                'contextTokens':131072,'sandbox':{'mode':'off'}},'list':[{'id':'pixel','default':True,
                'workspace':str(world.home / 'workspace'),
                'tools':{'exec':{'host':'gateway','security':'full','ask':'off'}}}]},
            'tools':{'allow':['pixel_ods_mcp','tool_search','tool_describe','tool_call'],'toolSearch':{'enabled':True,'mode':'tools'}},
            'models':{'mode':'replace','providers':{'fixture':{'api':'openai-completions',
                'baseUrl':f'http://127.0.0.1:{responder.server_port}/v1','apiKey':'invented-only',
                'models':[{'id':'model','name':'Invented SSE fixture','contextWindow':131072,'maxTokens':2048,'reasoning':False,'input':['text']}]}}}})
        world.cli('plugins','registry','--refresh','--json')
        inspected = world.cli('plugins','inspect','pixel-ods','--runtime','--json')
        self.assertEqual(inspected['plugin']['status'], 'loaded', inspected)
        world.start_gateway(port,'outcomes-native')
        user = 'ods-'+hashlib.sha256(b'owned-native-outcome-fixture').hexdigest()

        def post(path, body):
            request = urllib.request.Request(f'http://127.0.0.1:{port}{path}',data=json.dumps(body).encode(),
                headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
            with urllib.request.urlopen(request,timeout=35) as response: return json.load(response)

        original = {'action':'call','server':'invented','tool':'write','arguments':{'value':'first'}}
        plans.extend([{'id':'unknown-first','args':original}, {'id':'repeat-same','args':original},
            {'id':'repeat-changed','args':{**original,'arguments':{'value':'changed'}}},
            {'id':'safe-read','args':{'action':'call','server':'app:documents','tool':'search','arguments':{'query':'invented'}}},
            {'text':'The unrelated answer is 42. Invented model success claim.'}])
        first = post('/v1/chat/completions',{'model':'openclaw:pixel','user':user,'stream':False,
            'messages':[{'role':'user','content':'Use the invented fixture MCP write tool, then read the invented document and answer 6 times 7. No other tools.'}]})
        log = (world.home / 'outcomes-native.log').read_text()
        self.assertEqual(len(effects),1,{'log':log[-5000:],
            'events':events.read_text() if events.exists() else '',
            'tools':[[m for m in p.get('messages',[]) if m.get('role')=='tool'] for p in provider_calls]})
        self.assertEqual(len(reads),1,submissions)
        trace = [json.loads(line) for line in events.read_text().splitlines()]
        self.assertEqual([row['code'] for row in trace if row['phase']=='direct-repeat'],['integration-outcome-unresolved'])
        executed = [row for row in trace if row['phase']=='execute']
        self.assertEqual(len(executed),2,trace)  # First call and safe read only.
        verification = post('/pixel-ods/verification',{'runId':first['id']})
        self.assertIn('Its result could not be verified',verification['integrationNotice'])
        self.assertIn('42',first['choices'][0]['message']['content'])
        backend_state['unknown'] = False
        plans.extend([{'id':'fresh-owner','args':original},{'text':'Invented fresh owner completion.'}])
        second = post('/v1/chat/completions',{'model':'openclaw:pixel','user':user,'stream':False,
            'messages':[{'role':'user','content':'Now make one new invented fixture MCP write request. No other tools.'}]})
        self.assertNotEqual(first['id'],second['id'])
        self.assertEqual(len(effects),2,submissions)
        self.assertNotIn('integrationNotice',post('/pixel-ods/verification',{'runId':second['id']}))
        self.assertNotIn('integrationNotice',post('/pixel-ods/verification',{'runId':first['id']}))
        backend_state['pending'] = True
        plans.extend([{'id':'pending-owner','args':original},
            {'text':'Unrelated answer 42. Invented false success claim, retained as model prose.'}])
        third = post('/v1/chat/completions',{'model':'openclaw:pixel','user':user,'stream':False,
            'messages':[{'role':'user','content':'Request one new invented action and answer 6 times 7. No other tools.'}]})
        self.assertEqual(len(effects),2,submissions)  # Pending proposal adds no effect.
        notice = post('/pixel-ods/verification',{'runId':third['id']})['integrationNotice']
        self.assertIn('This call did not run the action; it requested approval',notice)
        self.assertIn('then ask Portal to retry the same action',notice)
        self.assertNotIn('owned_fixture_pending',notice)
        self.assertIn('42',third['choices'][0]['message']['content'])


if __name__ == '__main__':
    unittest.main()
