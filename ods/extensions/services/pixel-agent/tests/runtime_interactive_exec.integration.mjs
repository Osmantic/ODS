// Actual pinned SDK defaults and owned completion queue; no model inference.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {pathToFileURL} from 'node:url';
import {spawnSync} from 'node:child_process';
import {setTimeout as delay} from 'node:timers/promises';

const installed=process.env.OPENCLAW_PACKAGE;
const image=process.env.OPENCLAW_TEST_SANDBOX_IMAGE;
for(const host of ['gateway','sandbox']) test(`interactive execution policy on actual ${host} SDK`,
  {skip:!installed||(host==='sandbox'&&!image),timeout:90000},async()=>{
  assert.equal(JSON.parse(fs.readFileSync(path.join(installed,'package.json'))).version,'2026.6.33');
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'ods-interactive-exec-'));
  const workspace=path.join(root,'workspace');fs.mkdirSync(workspace);
  // Docker must not create this host bind source as root during sandbox setup.
  fs.mkdirSync(path.join(workspace,'.openclaw','sandbox-skills'),{recursive:true});
  process.env.OPENCLAW_CONFIG_PATH=path.join(root,'openclaw.json');
  process.env.OPENCLAW_STATE_DIR=root;
  const sessionKey='agent:pixel:openai-user:ods-'+'a'.repeat(64);
  const config={agents:{defaults:{workspace,sandbox:{mode:host==='sandbox'?'all':'off',scope:'session',workspaceAccess:'rw',
    docker:{image,env:{ODS_EXEC_CUSTODY:'1'},containerPrefix:'ods-interactive-',network:'none',readOnlyRoot:true,
      user:`${process.getuid()}:${process.getgid()}`,capDrop:['ALL'],pidsLimit:64,memory:'256m',cpus:1}}},
    list:[{id:'pixel',workspace,tools:{exec:{backgroundMs:60000,notifyOnExit:true,notifyOnExitEmptySuccess:true}}}]},
    plugins:{enabled:false},tools:{profile:'coding',exec:{host,security:'full',ask:'off'}}};
  fs.writeFileSync(process.env.OPENCLAW_CONFIG_PATH,JSON.stringify(config));
  fs.writeFileSync(path.join(workspace,'finite.py'),'import time\ntime.sleep(12)\nprint("finite-done")\n');
  fs.writeFileSync(path.join(workspace,'empty.py'),'import time\ntime.sleep(.2)\n');
  const sdk=await import(pathToFileURL(path.join(installed,'dist/plugin-sdk/agent-harness.js')));
  const events=await import(pathToFileURL(path.join(installed,'dist/system-events-DNR0hK3x.js')));
  events.d();
  let sandbox;
  const keepAlive=setInterval(()=>{},1000);
  try {
    sandbox=await sdk.resolveSandboxContext({config,sessionKey,workspaceDir:workspace});
    const scope={agentId:'pixel',runId:path.basename(root),sessionId:path.basename(root),sessionKey};
    const create=(backgroundMs)=>sdk.createOpenClawCodingTools({...scope,config,workspaceDir:workspace,cwd:workspace,sandbox,
      ...(backgroundMs===undefined?{}:{exec:{backgroundMs}})}).find(t=>t.name==='exec');
    const cwd=host==='sandbox'?'/workspace':workspace;
    const outcomes=[];
    for(const [name,backgroundMs] of [['baseline',10000],['candidate',undefined]]) {
      const tool=create(backgroundMs),start=performance.now();
      const result=await tool.execute(name,{command:'python3 finite.py',workdir:cwd,timeout:25});
      const returnedMs=performance.now()-start;
      assert.equal(result.details.status,name==='baseline'?'running':'completed');
      if(name==='baseline') {
        const deadline=performance.now()+10000;
        while(events.u(sessionKey).length===0&&performance.now()<deadline)await delay(50);
        assert.equal(events.u(sessionKey).length,1,'one background completion in the exact owning session');
        assert.match(events.u(sessionKey)[0],/finite-done/);
        assert.equal(events.u('agent:other:main').length,0);
        events.i(sessionKey);
      } else {
        assert.match(result.details.aggregated,/finite-done/);
        assert.equal(events.u(sessionKey).length,0,'foreground completion needs no background notification');
      }
      outcomes.push({name,returnedMs,status:result.details.status});
    }
    const background=await create().execute('empty-background',{command:'python3 empty.py',workdir:cwd,timeout:10,background:true});
    assert.equal(background.details.status,'running','explicit background is preserved');
    const deadline=performance.now()+10000;
    while(events.u(sessionKey).length===0&&performance.now()<deadline)await delay(50);
    assert.equal(events.u(sessionKey).length,1,'empty success is delivered once');
    await delay(300);
    assert.equal(events.i(sessionKey).length,1);
    assert.equal(events.i(sessionKey).length,0,'completion cannot be consumed twice');
    console.log(JSON.stringify({host,outcomes,ownedEmptyCompletion:true}));
  } finally {
    clearInterval(keepAlive);events.d();
    if(sandbox){assert.match(sandbox.containerName,/^ods-interactive-/);const removed=spawnSync('docker',['rm','-f',sandbox.containerName],{encoding:'utf8'});assert.equal(removed.status,0,removed.stderr);}
    fs.rmSync(root,{recursive:true,force:true});
  }
});
