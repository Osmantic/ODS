"""Real pinned native HTTP tools keep ODS admission until each execution ends.

The gateway, plugin hooks and tool execution are real; only the disposable tool
waits on private fixture files. No model, installed owner state or account is used.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

import test_connection_plugin_runtime as fixture

ROOT = Path(__file__).resolve().parents[4]
PROBE = r'''
import fs from 'node:fs';
import path from 'node:path';
export default {id:'ods-direct-probe', register(api) {
  api.registerTool({name:'ods_qa_direct',description:'Private lifecycle fixture.',
    parameters:{type:'object',properties:{slot:{type:'string'},behavior:{type:'string'}},required:['slot','behavior'],additionalProperties:false},
    async execute(_id, args) {
      if (!/^[a-z]+$/.test(args.slot)) throw new Error('invalid fixture slot');
      const root=process.env.ODS_QA_DIRECT_ROOT;
      fs.writeFileSync(path.join(root,'started-'+args.slot),'started',{mode:0o600});
      if (args.behavior==='throw') throw new Error('controlled tool failure');
      if (args.behavior==='wait') {
        const deadline=Date.now()+15000;
        while(!fs.existsSync(path.join(root,'release-'+args.slot))) {
          if(Date.now()>deadline) throw new Error('fixture release timeout');
          await new Promise(resolve=>setTimeout(resolve,10));
        }
      }
      return {content:[{type:'text',text:'controlled completion'}]};
    }});
}};
'''


@unittest.skipUnless(fixture.PACKAGE and os.name == 'posix', 'Requires pinned native OpenClaw')
class NativeDirectToolCompletionTests(unittest.TestCase):
    def test_success_failure_and_overlapping_native_calls_complete_their_admission(self):
        temporary = tempfile.TemporaryDirectory(prefix='ods-direct-native-')
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        package = directory / 'openclaw'
        source_module = Path(fixture.PACKAGE) / 'dist/tools-invoke-shared-dvxhZAis.js'
        source_digest = hashlib.sha256(source_module.read_bytes()).hexdigest()
        self.addCleanup(lambda: self.assertEqual(hashlib.sha256(source_module.read_bytes()).hexdigest(),source_digest))
        # Patch only this owned copy. Shared pinned bytes and installed runtime
        # remain unchanged; dependency resolution uses the existing pinned tree.
        shutil.copytree(Path(fixture.PACKAGE), package, ignore=shutil.ignore_patterns('node_modules'))
        bundled_dependencies = Path(fixture.PACKAGE) / 'node_modules'
        if bundled_dependencies.is_dir():
            (package / 'node_modules').symlink_to(bundled_dependencies, target_is_directory=True)
        (directory / 'node_modules').symlink_to(Path(fixture.PACKAGE).parent, target_is_directory=True)
        if os.environ.get('ODS_DIRECT_TOOL_PATCH', '1') != '0':
            patched = subprocess.run([sys.executable, str(ROOT / 'extensions/services/pixel-agent/host/openclaw_tool_recovery.py'),
                '--openclaw-bin',str(package/'openclaw.mjs'),'--direct-tool-completion',
                '--state-dir',str(directory/'repair')],capture_output=True,text=True,check=True)
            receipt = json.loads(patched.stdout)
            self.assertEqual(receipt['module'],'tools-invoke-shared-dvxhZAis.js')
            self.assertEqual(receipt['status'],'changed')
        fixture.PluginRuntimeTests.setUpClass()
        world = fixture.PluginRuntimeTests()
        world.setUp()
        self.addCleanup(world.doCleanups)
        world.openclaw = str(package / 'openclaw.mjs')
        world.env['ODS_QA_DIRECT_ROOT'] = str(world.home)
        plugin = world.home / 'portal-plugin'
        shutil.copytree(ROOT / 'extensions/services/pixel-agent/plugin', plugin)
        for entry in [plugin, *plugin.rglob('*')]:
            if not entry.is_symlink():
                entry.chmod(0o755 if entry.is_dir() else 0o644)
        probe = world.home / 'probe-plugin'
        probe.mkdir(mode=0o700)
        (probe / 'package.json').write_text(json.dumps({'name':'ods-direct-probe','version':'1.0.0','type':'module',
                                                     'openclaw':{'extensions':['./index.mjs']}}))
        (probe / 'openclaw.plugin.json').write_text(json.dumps({'id':'ods-direct-probe',
            'contracts':{'tools':['ods_qa_direct']},
            'configSchema':{'type':'object','additionalProperties':False}}))
        (probe / 'index.mjs').write_text(PROBE)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0))
            port = listener.getsockname()[1]
        token = secrets.token_hex(24)
        world.write_config({'gateway':{'mode':'local','bind':'loopback','port':port,'auth':{'mode':'token','token':token}},
            'models':{'providers':{'ods-local':{'api':'openai-completions','apiKey':'local-no-auth',
                'baseUrl':'http://127.0.0.1:1/v1','models':[{'id':'fixture','name':'Local fixture',
                'contextWindow':16384,'maxTokens':2048,'reasoning':False,'input':['text']}]}}},
            'plugins':{'allow':['pixel-ods','ods-direct-probe'],'load':{'paths':[str(plugin),str(probe)]},
                       'entries':{'pixel-ods':{'enabled':True,'hooks':{'allowConversationAccess':True},'config':{}},
                                  'ods-direct-probe':{'enabled':True}}},
            'agents':{'defaults':{'heartbeat':{'every':'0m'},'sandbox':{'mode':'off'}},
                      'list':[{'id':'pixel','default':True,'workspace':str(world.home/'workspace'),'model':'ods-local/fixture',
                               'tools':{'exec':{'host':'gateway','security':'full','ask':'off'}}}]},
            'tools':{'allow':['ods_qa_direct']}})
        world.cli('plugins','registry','--refresh','--json')
        primary = world.cli('plugins','inspect','pixel-ods','--runtime','--json')['plugin']
        self.assertEqual(primary['status'],'loaded',primary.get('error'))
        self.assertIs(primary['imported'],True)
        probe_status = world.cli('plugins','inspect','ods-direct-probe','--runtime','--json')['plugin']
        self.assertEqual(probe_status['status'],'loaded',probe_status.get('error'))
        world.start_gateway(port,'direct')

        def request(path, body=None):
            req = urllib.request.Request('http://127.0.0.1:%d%s' % (port,path),
                data=json.dumps(body).encode() if body is not None else None,
                headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
            try:
                with urllib.request.urlopen(req,timeout=25) as response:
                    raw = response.read()
                    self.assertIn('application/json',response.headers.get('Content-Type',''),
                                  (response.status,raw[:300],(world.home/'direct.log').read_text()[-3000:]))
                    return response.status,json.loads(raw)
            except urllib.error.HTTPError as error:
                return error.code,json.load(error)

        def status():
            code, body = request('/pixel-ods/access-runtime')
            self.assertEqual(code,200)
            self.assertTrue(body['available'], body)
            return body

        def invoke(slot,behavior='success',identity=None):
            payload = {'tool':'ods_qa_direct','args':{'slot':slot,'behavior':behavior},'sessionKey':'agent:pixel:main'}
            if identity:
                payload['idempotencyKey'] = identity
            return request('/tools/invoke',payload)

        self.assertEqual(status()['active'],0)
        code, outcome = invoke('success')
        self.assertEqual(code,200,(outcome,(world.home/'direct.log').read_text()[-3000:]))
        self.assertEqual(status()['phase'],'idle')
        self.assertEqual(invoke('failure','throw')[0],500)
        self.assertEqual(status()['active'],0)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(invoke,'first','wait','same-native-id')
            second = pool.submit(invoke,'second','wait','same-native-id')
            try:
                deadline = time.monotonic()+10
                while not all((world.home/('started-'+slot)).exists() for slot in ('first','second')):
                    self.assertLess(time.monotonic(),deadline)
                    time.sleep(.02)
                self.assertEqual(status()['activity']['tools'],2)
                (world.home/'release-first').touch()
                self.assertEqual(first.result(timeout=10)[0],200)
                snapshot = status()
                self.assertEqual(snapshot['activity']['tools'],1)
                lease = secrets.token_hex(32)
                self.assertEqual(request('/pixel-ods/access-runtime',
                    {'operation':'acquire','token':lease,'revision':snapshot['revision']})[0],409)
            finally:
                (world.home/'release-first').touch()
                (world.home/'release-second').touch()
            self.assertEqual(second.result(timeout=10)[0],200)
        snapshot = status()
        self.assertEqual((snapshot['phase'],snapshot['active']),('idle',0))
        code, held = request('/pixel-ods/access-runtime',
            {'operation':'acquire','token':lease,'revision':snapshot['revision']})
        self.assertEqual(code,200,held)
        self.assertEqual(invoke('blocked')[0],403)
        self.assertFalse((world.home/'started-blocked').exists())
        self.assertEqual(status()['active'],0)
        code, released = request('/pixel-ods/access-runtime',
            {'operation':'release','token':lease,'revision':held['revision']})
        self.assertEqual(code,200,released)
        self.assertEqual(status()['phase'],'idle')


if __name__ == '__main__':
    unittest.main()
