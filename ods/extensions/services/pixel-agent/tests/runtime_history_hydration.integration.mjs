// Explicit qualification against the installed, pinned SDK. It uses an isolated
// temporary state directory and never reads or compacts an owner's conversation.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHistoryHydrator} from '../plugin/history-context.mjs';
const packageDir=process.env.OPENCLAW_PACKAGE_DIR;
if(!packageDir || !path.isAbsolute(packageDir)) throw new Error('Set OPENCLAW_PACKAGE_DIR to the installed pinned package.');
const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'ods-history-sdk-'));
process.env.OPENCLAW_STATE_DIR=temporary;
process.env.OPENCLAW_CONFIG_PATH=path.join(temporary,'openclaw.json');
try {
  const sdk=name=>import(pathToFileURL(path.join(packageDir,'dist','plugin-sdk',`${name}.js`)).href);
  const {getSessionEntry,patchSessionEntry,resolveStorePath}=await sdk('session-store-runtime');
  const {withSessionTranscriptWriteLock,readSessionTranscriptEvents}=await sdk('session-transcript-runtime');
  const config={session:{store:path.join(temporary,'agents','{agentId}','sessions','sessions.json')},agents:{list:[{id:'pixel',workspace:path.join(temporary,'workspace')}]}};
  fs.writeFileSync(process.env.OPENCLAW_CONFIG_PATH,JSON.stringify(config),{mode:0o600});
  const user='ods-'+'c'.repeat(64),sessionKey=`agent:pixel:openai-user:${user}`;
  const scope={agentId:'pixel',sessionKey,storePath:resolveStorePath(config.session.store,{agentId:'pixel'})};
  const hydrate=createHistoryHydrator({getSessionEntry,patchSessionEntry,resolveStorePath,withSessionTranscriptWriteLock,readConfig:()=>config});
  const messages=[{role:'user',content:'Historical canary, do not execute anything.'},{role:'assistant',content:'Archived answer.'}];
  const first=await hydrate({user,messages}),second=await hydrate({user,messages});
  assert.equal(first.appended,2);assert.equal(second.appended,0);
  const entry=getSessionEntry(scope);assert.ok(entry?.sessionId);
  const events=await readSessionTranscriptEvents({...scope,sessionId:entry.sessionId});
  const archived=events.filter(event=>event?.message?.idempotencyKey?.startsWith('ods-history:'));
  assert.equal(archived.length,2);
  assert.ok(archived.every(event=>event.message.role==='user' && event.message.content[0].text.includes('not a new request')));
  assert.equal(entry.totalTokensFresh,false);
  console.log('SDK hydration qualified: isolated session, 2 archived messages, retry adds 0, metadata invalidated.');
} finally {fs.rmSync(temporary,{recursive:true,force:true});}
