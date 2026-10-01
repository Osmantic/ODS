import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createAccessRuntime} from '../plugin/access-runtime.mjs';
import {createContextCompaction} from '../plugin/context-compaction.mjs';

const posix = {skip: process.platform === 'win32'};
const token = 'a'.repeat(64), next = 'b'.repeat(64);
const user = `ods-${'a'.repeat(64)}`, sessionKey = `agent:pixel:openai-user:${user}`;
const tick = () => new Promise(resolve => setImmediate(resolve));

async function fixture(t, {ok=true, reject=false}={}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ods-maintenance-proof-'));
  fs.chmodSync(root, 0o700);
  t.after(() => fs.rmSync(root, {recursive:true,force:true}));
  let config = {agents:{list:[{id:'pixel',workspace:root,model:'cloud/test',sandbox:{mode:'off'}}]},
    tools:{exec:{host:'gateway'}},models:{providers:{cloud:{models:[{id:'test',contextWindow:32768}]}}}};
  let current = config, clock = Date.now();
  const runtime = createAccessRuntime({directory:path.join(root,'runtime'),probeDirectory:root,
    runtimeVersion:'2026.6.33',hooksAllowed:true,config:()=>config,settingsConfig:()=>current,now:()=>clock,
    resolveSandbox:async()=>({enabled:false}),
    execControl:()=>({prepare:(_id,command)=>command,signal(){},clear(){}}),
    createTools:()=>[
      {name:'exec',execute:async(_id,{command})=>command==='sleep 30'
        ? {details:{exitCode:130}}
        : {content:[{type:'text',text:command.match(/[a-f0-9]{64}/)[0]}],details:{status:'completed'}}},
      {name:'write',execute:async(_id,{path:target,content})=>{fs.writeFileSync(target,content);return {};}}
    ]});
  runtime.acquire(token,runtime.status().revision);
  await runtime.probe(token);
  runtime.release(token);
  const proof=structuredClone(runtime.status().proof);
  assert.equal(proof.executed,true);
  const acquire=(lease,revision)=>(runtime.acquireMaintenance ?? runtime.acquire)(lease,revision);
  const release=lease=>(runtime.releaseMaintenance ?? runtime.release)(lease);
  const compactor=createContextCompaction({directory:path.join(root,'compaction'),requireModelObservation:false,
    readConfig:()=>config,readSession:()=>({sessionId:'fixture-session',modelProvider:'cloud',model:'test',
      contextTokens:32768,totalTokens:123,totalTokensFresh:true,updatedAt:1}),
    activeSession:()=>false,prepareModel:async()=>({close:async()=>{}}),
    admission:{status:runtime.status,acquire,release,owns:runtime.owns},
    callGateway:async()=>{if(reject) throw Error('fixture transport failure');return {key:sessionKey,ok,compacted:false};},
  });
  return {root,runtime,proof,acquire,release,compactor,get config(){return config;},
    change(){config=structuredClone(config);config.tools.exec.host='sandbox';config.agents.list[0].sandbox.mode='all';current=config;},
    changeCurrent(){current={...current,tools:{exec:{host:'sandbox'}}};},
    expire(){clock+=1920001;}};
}

test('skipped real compactor preserves the previously executed access proof',posix,async t=>{
  const f=await fixture(t);
  await f.compactor.compact(user,'skip');await tick();await tick();
  assert.equal(f.compactor.context(user).compaction.status,'skipped');
  assert.equal(f.runtime.status().phase,'idle');
  assert.deepEqual(f.runtime.status().proof,f.proof);
});

test('failed maintenance callback restores proof without inventing success',posix,async t=>{
  const f=await fixture(t);
  await assert.rejects(f.compactor.withMaintenance(user,async()=>{throw Error('fixture failure');}));
  assert.equal(f.runtime.status().phase,'idle');
  assert.deepEqual(f.runtime.status().proof,f.proof);
});

test('confirmed failed compaction releases its hold and preserves unchanged authority',posix,async t=>{
  const f=await fixture(t,{ok:false});await f.compactor.compact(user,'failed');await tick();await tick();
  assert.equal(f.compactor.context(user).compaction.status,'failed');
  assert.equal(f.runtime.status().phase,'idle');assert.deepEqual(f.runtime.status().proof,f.proof);
});

test('unknown compaction keeps admission held and proof unavailable',posix,async t=>{
  const f=await fixture(t,{reject:true});await f.compactor.compact(user,'unknown');await tick();await tick();
  assert.equal(f.compactor.context(user).compaction.status,'unknown');
  assert.equal(f.runtime.status().phase,'held');assert.equal(f.runtime.status().proof,null);
});

test('changed authority during maintenance never restores previous proof',posix,async t=>{
  for(const change of ['change','changeCurrent','expire']){
    await t.test(change,async t=>{
      const f=await fixture(t);f.acquire(next,f.runtime.status().revision);f[change]();
      assert.equal(f.release(next).proof,null);
    });
  }
});

test('normal transition discards a maintenance snapshot even with the same token',posix,async t=>{
  const f=await fixture(t);f.acquire(next,f.runtime.status().revision);
  f.runtime.acquire(next,f.runtime.status().revision);
  f.release(next);assert.equal(f.runtime.status().proof,null);
  f.runtime.acquire(token,f.runtime.status().revision);
  f.runtime.release(token);assert.equal(f.runtime.status().proof,null);
});

test('wrong token cannot release maintenance or expose its saved proof',posix,async t=>{
  const f=await fixture(t);f.acquire(next,f.runtime.status().revision);
  assert.equal(f.runtime.status().proof,null);
  assert.throws(()=>f.release(token));assert.equal(f.runtime.status().phase,'held');
  assert.deepEqual(f.release(next).proof,f.proof);
});

test('a new runtime reading a persisted hold has no maintenance proof to restore',posix,async t=>{
  const f=await fixture(t);f.acquire(next,f.runtime.status().revision);
  const directory=path.join(f.root,'new-instance');fs.mkdirSync(directory,{mode:0o700});
  const persisted=fs.readFileSync(path.join(f.root,'runtime','state.json'));
  assert.equal(persisted.includes(Buffer.from('proof')),false);
  fs.writeFileSync(path.join(directory,'state.json'),persisted,{mode:0o600});
  const replacement=createAccessRuntime({directory,runtimeVersion:'2026.6.33',hooksAllowed:true,
    config:()=>f.config,settingsConfig:()=>f.config});
  assert.equal(replacement.status().available,true);assert.equal(replacement.status().proof,null);
  assert.equal(replacement.releaseMaintenance(next).proof,null);
});

test('failed probe invalidates the saved maintenance proof',posix,async t=>{
  const f=await fixture(t);f.acquire(next,f.runtime.status().revision);f.change();
  await assert.rejects(f.runtime.probe(next));
  assert.equal(f.release(next).proof,null);
});

test('a different managed owner cannot restore proof or reopen the maintenance hold',posix,async t=>{
  const f=await fixture(t), first={}, replacement={};
  f.runtime.acquireMaintenance(next,f.runtime.status().revision,first);
  assert.throws(()=>f.runtime.releaseMaintenance(next,replacement));
  assert.equal(f.runtime.status().phase,'held');assert.equal(f.runtime.status().proof,null);
  f.runtime.release(next);assert.equal(f.runtime.status().proof,null);
});

test('idempotent maintenance acquisition does not extend proof lifetime',posix,async t=>{
  const f=await fixture(t);f.acquire(next,f.runtime.status().revision);f.expire();
  f.acquire(next,f.runtime.status().revision);assert.equal(f.release(next).proof,null);
});

test('normal release does not restore a captured maintenance proof',posix,async t=>{
  const f=await fixture(t);f.acquire(next,f.runtime.status().revision);
  assert.equal(f.runtime.release(next).proof,null);
});

test('production compaction wiring selects maintenance APIs; HTTP keeps transition APIs',()=>{
  const source=fs.readFileSync(new URL('../plugin/index.js',import.meta.url),'utf8').replace(/\r\n/g,'\n');
  const start=source.indexOf('admission:{status:'),end=source.indexOf('},\n    });',start);
  assert.ok(start>0 && end>start);
  const calls=[];
  const owner={status:()=>({}),owns:()=>true,acquireMaintenance:()=>calls.push('acquire'),
    releaseMaintenance:()=>calls.push('release')};
  for(const managed of [null,owner]){
    const admission=Function('currentManagedRuntime','accessRuntime',
      'return ('+source.slice(start+'admission:'.length,end+1)+')')(managed,owner);
    admission.acquire(next,'revision');admission.release(next);
  }
  assert.deepEqual(calls,['acquire','release','acquire','release']);
  const route=source.slice(source.indexOf('path: "/pixel-ods/access-runtime"'));
  assert.ok(route.includes('managedRuntime.acquireTransition(value.token, value.revision)'));
  assert.ok(route.includes('value.operation === "release") result = accessRuntime.release(value.token)'));
  assert.equal(route.includes('value.operation === "acquireMaintenance"'),false);
});
