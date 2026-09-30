// No gateway service or model is contacted. Execute the pinned SDK and native
// sessions.delete handler against a process-private temporary state directory.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {createConversationImageLifecycle} from '../plugin/conversation-image-lifecycle.mjs';
import {createHistoryHydrator} from '../plugin/history-context.mjs';
import {createAccessRuntime} from '../plugin/access-runtime.mjs';
import {createContextCompaction} from '../plugin/context-compaction.mjs';

const packageRoot=process.env.OPENCLAW_PACKAGE;
assert.ok(packageRoot,'Set OPENCLAW_PACKAGE to the installed pinned package (read-only).');
assert.equal(JSON.parse(fs.readFileSync(path.join(packageRoot,'package.json'))).version,'2026.6.33');
const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'ods-native-image-delete-'));fs.chmodSync(temporary,0o700);
process.env.OPENCLAW_STATE_DIR=temporary;
process.env.OPENCLAW_CONFIG_PATH=path.join(temporary,'openclaw.json');
const sessions=path.join(temporary,'agents','pixel','sessions');fs.mkdirSync(sessions,{recursive:true,mode:0o700});
const config={agents:{list:[{id:'pixel',default:true,workspace:temporary,model:'cloud/test',sandbox:{mode:'off'}}]},
  tools:{exec:{host:'gateway'}},models:{providers:{cloud:{baseUrl:'http://127.0.0.1:1/v1',models:[{id:'test',name:'Offline fixture',contextWindow:32768}]}}},
  session:{store:path.join(sessions,'sessions.json')}};
fs.writeFileSync(process.env.OPENCLAW_CONFIG_PATH,JSON.stringify(config),{mode:0o600});
const sdk=await import(pathToFileURL(path.join(packageRoot,'dist/plugin-sdk/session-store-runtime.js')));
const transcriptSdk=await import(pathToFileURL(path.join(packageRoot,'dist/plugin-sdk/session-transcript-runtime.js')));
const {sessionsHandlers}=await import(pathToFileURL(path.join(packageRoot,'dist/sessions-KE_Xmzwf.js')));
test.after(()=>fs.rmSync(temporary,{recursive:true,force:true}));

test('pinned SDK creates missing session, survives reload, then native delete archives by rename and lifecycle purges exact bytes',async()=>{
  const user=`ods-${'4'.repeat(64)}`,sessionKey=`agent:pixel:openai-user:${user}`;
  const calls=[];
  // Run the real access proof/maintenance state machine with deterministic tool
  // adapters; no host command or live runtime is executed by this fixture.
  const access=createAccessRuntime({directory:path.join(temporary,'access'),probeDirectory:temporary,
    runtimeVersion:'2026.6.33',hooksAllowed:true,config:()=>config,settingsConfig:()=>config,
    resolveSandbox:async()=>({enabled:false}),
    execControl:()=>({prepare:(_id,command)=>command,signal(){},clear(){}}),
    createTools:()=>[
      {name:'exec',execute:async(_id,{command})=>command==='sleep 30'?{details:{exitCode:130}}:
        {content:[{type:'text',text:command.match(/[a-f0-9]{64}/)[0]}],details:{status:'completed'}}},
      {name:'write',execute:async(_id,{path:target,content})=>{fs.writeFileSync(target,content);return {};}}
    ]});
  const token='a'.repeat(64);access.acquire(token,access.status().revision);await access.probe(token);access.release(token);
  const proof=structuredClone(access.status().proof);assert.equal(proof.executed,true);
  const compactor=createContextCompaction({directory:path.join(temporary,'compaction'),requireModelObservation:false,
    readConfig:()=>config,readSession:scope=>sdk.getSessionEntry({...scope,storePath:config.session.store}),
    activeSession:()=>false,prepareModel:async()=>({close:async()=>{}}),
    admission:{status:access.status,acquire:access.acquireMaintenance,release:access.releaseMaintenance,owns:access.owns},
    callGateway:async()=>{throw Error('This fixture must not compact');}});
  const dependencies={readConfig:()=>config,...sdk,compactor,
    callGateway:async(method,_options,params)=>{
      calls.push(params);
      let result,error;
      await sessionsHandlers[method]({params,client:null,isWebchatConnect:()=>false,
        context:{getRuntimeConfig:()=>config,broadcast:()=>{},broadcastToConnIds:()=>{},getSessionEventSubscriberConnIds:()=>new Set()},
        respond:(ok,value,failure)=>{if(!ok)error=failure;else result=value;}});
      if(error)throw new Error(JSON.stringify(error));return result;
    }};
  let lifecycle=createConversationImageLifecycle(dependencies);
  assert.equal(sdk.getSessionEntry({agentId:'pixel',sessionKey,storePath:config.session.store}),undefined);
  await lifecycle.bind(user);
  assert.equal(access.status().phase,'idle');assert.deepEqual(access.status().proof,proof);
  const entry=sdk.getSessionEntry({agentId:'pixel',sessionKey,storePath:config.session.store});
  assert.match(entry.sessionId,/^[a-f0-9-]{36}$/);assert.equal(entry.sessionFile,undefined);
  const hydrate=createHistoryHydrator({...sdk,...transcriptSdk,readConfig:()=>config});
  assert.equal((await hydrate({user,messages:[{role:'user',content:'Prior textual history'}]})).hydrated,true);
  assert.equal(sdk.getSessionEntry({agentId:'pixel',sessionKey,storePath:config.session.store}).sessionId,entry.sessionId);
  const transcript=path.join(sessions,`${entry.sessionId}.jsonl`);
  await transcriptSdk.withSessionTranscriptWriteLock({agentId:'pixel',sessionKey,sessionId:entry.sessionId,storePath:config.session.store,config},async({appendMessage,publishUpdate})=>{
    assert.ok(await appendMessage({message:{role:'user',content:[{type:'image',mimeType:'image/png',data:'PRIVATE_PIXEL_BYTES'}],timestamp:Date.now()}}));await publishUpdate();
  });
  assert.match(fs.readFileSync(transcript,'utf8'),/PRIVATE_PIXEL_BYTES/);
  lifecycle.observe({sessionKey,sessionId:entry.sessionId});
  const foreign=path.join(sessions,'do-not-delete.txt');fs.writeFileSync(foreign,'foreign');
  lifecycle=createConversationImageLifecycle(dependencies);
  assert.deepEqual(await lifecycle.purge(user),{schemaVersion:1,deleted:true});
  assert.equal(access.status().phase,'idle');assert.deepEqual(access.status().proof,proof);
  assert.equal(sdk.getSessionEntry({agentId:'pixel',sessionKey,storePath:config.session.store}),undefined);
  assert.equal(fs.readdirSync(sessions).some(name=>name.startsWith(entry.sessionId)),false);
  assert.equal(fs.readFileSync(foreign,'utf8'),'foreign');assert.equal(calls.length,1);
  assert.deepEqual(await lifecycle.purge(user),{schemaVersion:1,deleted:true});assert.equal(calls.length,1);
});
