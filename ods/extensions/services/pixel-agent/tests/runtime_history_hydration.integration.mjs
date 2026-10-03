// Explicit qualification against the installed, pinned SDK. It uses an isolated
// temporary state directory and never reads or compacts an owner's conversation.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import vm from 'node:vm';
import {createHistoryHydrator} from '../plugin/history-context.mjs';
const packageDir=process.env.OPENCLAW_PACKAGE_DIR;
if(!packageDir || !path.isAbsolute(packageDir)) throw new Error('Set OPENCLAW_PACKAGE_DIR to the installed pinned package.');
const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'ods-history-sdk-'));
process.env.OPENCLAW_STATE_DIR=temporary;
process.env.OPENCLAW_CONFIG_PATH=path.join(temporary,'openclaw.json');
try {
  const sdk=name=>import(pathToFileURL(path.join(packageDir,'dist','plugin-sdk',`${name}.js`)).href);
  const {getSessionEntry,patchSessionEntry,resolveStorePath}=await sdk('session-store-runtime');
  const {withSessionTranscriptWriteLock,readSessionTranscriptEvents,appendAssistantMirrorMessageByIdentity}=await sdk('session-transcript-runtime');
  const config={session:{store:path.join(temporary,'agents','{agentId}','sessions','sessions.json')},agents:{list:[{id:'pixel',workspace:path.join(temporary,'workspace')}]}};
  fs.writeFileSync(process.env.OPENCLAW_CONFIG_PATH,JSON.stringify(config),{mode:0o600});
  const user='ods-'+'c'.repeat(64),sessionKey=`agent:pixel:openai-user:${user}`;
  const scope={agentId:'pixel',sessionKey,storePath:resolveStorePath(config.session.store,{agentId:'pixel'})};
  const hydrate=createHistoryHydrator({getSessionEntry,patchSessionEntry,resolveStorePath,withSessionTranscriptWriteLock,appendAssistantMirrorMessageByIdentity,readConfig:()=>config});
  const messages=[{role:'user',content:'Historical canary, do not execute anything.'},{role:'assistant',content:'Archived answer.'}];
  const first=await hydrate({user,messages}),second=await hydrate({user,messages});
  assert.equal(first.appended,2);assert.equal(second.appended,0);
  const entry=getSessionEntry(scope);assert.ok(entry?.sessionId);
  const events=await readSessionTranscriptEvents({...scope,sessionId:entry.sessionId});
  const archived=events.filter(event=>event?.message?.idempotencyKey?.startsWith('ods-history:'));
  assert.equal(archived.length,2);
  assert.ok(archived.every(event=>event.message.role==='user' && event.message.content[0].text.includes('not a new request')));
  const seals=events.filter(event=>event?.message?.model==='delivery-mirror');
  assert.equal(seals.length,1);assert.equal(seals[0].message.usage.totalTokens,0);
  // Exercise the actual pinned startup function, not a mock of its decision.
  // Only its file/cache helpers are provided here; all writes stay in the
  // temporary fixture. The negative control reproduces the original erasure.
  const dist=path.join(packageDir,'dist');
  const runner=fs.readdirSync(dist).filter(f=>/^selection-.*\.js$/.test(f))
    .map(f=>fs.readFileSync(path.join(dist,f),'utf8')).find(s=>s.includes('async function prepareSessionManagerForRun('));
  assert.ok(runner,'pinned startup implementation is available');
  const start=runner.indexOf('async function prepareSessionManagerForRun(');
  const source=runner.slice(start,runner.indexOf('\n//#endregion',start));
  const prepare=vm.runInNewContext(`(${source})`,{fs$1:fs.promises,
    assertExistingHeaderIsReadable:async file=>assert.equal(JSON.parse(fs.readFileSync(file,'utf8').split('\n')[0]).type,'session'),
    invalidateSessionFileRepairCache:()=>{},
  });
  const header=events.find(e=>e.type==='session');assert.ok(header);
  for(const sealed of [false,true]) {
    const file=path.join(temporary,`startup-${sealed}.jsonl`);
    const entries=sealed?events:events.filter(e=>e.message?.model!=='delivery-mirror');
    fs.writeFileSync(file,entries.map(e=>JSON.stringify(e)).join('\n')+'\n');
    const manager={fileEntries:[...entries],byId:new Map(),labelsById:new Map()};
    await prepare({sessionManager:manager,hadSessionFile:true,sessionId:header.id,cwd:header.cwd,sessionFile:file});
    assert.equal(manager.fileEntries.some(e=>e.message?.idempotencyKey?.startsWith('ods-history:')),sealed);
    assert.equal(fs.readFileSync(file,'utf8').includes('Historical canary'),sealed);
  }
  assert.equal(entry.totalTokensFresh,false);
  console.log('SDK hydration qualified: 2 archived messages, idempotent zero-usage receipt, actual startup preserves sealed history and erases unsealed negative control.');
} finally {fs.rmSync(temporary,{recursive:true,force:true});}
