import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {randomUUID,createHash} from 'node:crypto';
import {createConversationImageLifecycle} from '../plugin/conversation-image-lifecycle.mjs';
import {createChatImageStore} from '../host/chat_image_store.mjs';
import {createChatHistoryLedger} from '../host/chat_history_ledger.mjs';

const user=`ods-${'a'.repeat(64)}`, other=`ods-${'b'.repeat(64)}`;
function setup(t) {
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'ods-image-lifecycle-'));fs.chmodSync(directory,0o700);
  t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  let entry=null,busy=false,lostReply=false;
  const calls=[];
  const dependencies={readConfig:()=>({}),resolveStorePath:()=>path.join(directory,'sessions.json'),
    getSessionEntry:()=>entry,patchSessionEntry:async({fallbackEntry})=>{entry??=fallbackEntry;},
    compactor:{withMaintenance:async(_user,fn)=>{if(busy)throw new Error('busy');return fn();}},
    callGateway:async(method,options,args)=>{
      calls.push({method,options,args});
      const file=path.join(directory,`${entry.sessionId}.jsonl`);
      if(fs.existsSync(file))fs.renameSync(file,file+'.deleted.2026-09-30T00-00-00.000Z');
      entry=null;if(lostReply)throw new Error('connection lost');
      return {ok:true,key:args.key,deleted:true};
    }};
  const module=createConversationImageLifecycle(dependencies);
  const transcript=(id=entry.sessionId,content='private image bytes')=>{
    const file=path.join(directory,`${id}.jsonl`);
    fs.writeFileSync(file,JSON.stringify({type:'session',id,version:3,timestamp:'2026-09-30'})+'\n'+JSON.stringify({type:'message',message:{content:[{type:'image',data:content}]}})+'\n',{mode:0o600});return file;
  };
  return {directory,module,restart:()=>createConversationImageLifecycle(dependencies),calls,transcript,get entry(){return entry;},set entry(value){entry=value;},set busy(value){busy=value;},set lostReply(value){lostReply=value;}};
}
test('registered native files and same-inode archives purge, foreign transcript and workspace stay intact',async t=>{
  const f=setup(t);await f.module.bind(user);
  const id=f.entry.sessionId,file=f.transcript();f.module.observe({sessionKey:`agent:pixel:openai-user:${user}`,sessionId:id});
  const foreign=f.transcript(randomUUID(),'foreign');const workspace=path.join(f.directory,'project.txt');fs.writeFileSync(workspace,'keep');
  assert.deepEqual(await f.module.purge(user),{schemaVersion:1,deleted:true});
  assert.equal(fs.existsSync(file),false);assert.equal(fs.readdirSync(f.directory).some(name=>name.startsWith(id)),false);
  assert.equal(fs.existsSync(foreign),true);assert.equal(fs.readFileSync(workspace,'utf8'),'keep');
  assert.equal(f.calls.length,1);assert.equal(f.calls[0].method,'sessions.delete');
  assert.deepEqual(await f.module.purge(user),{schemaVersion:1,deleted:true});assert.equal(f.calls.length,1);
  await assert.rejects(f.module.bind(user),/unavailable/);
  assert.throws(()=>f.module.observe({sessionKey:`agent:pixel:openai-user:${user}`,sessionId:id}),/unavailable/);
});
test('lost native delete receipt is retried from durable binding after restart without deleting another session',async t=>{
  const f=setup(t);await f.module.bind(user);const id=f.entry.sessionId;f.transcript();f.lostReply=true;
  await assert.rejects(f.module.purge(user),/connection lost/);
  const state=JSON.parse(fs.readFileSync(path.join(f.directory,'.ods-image-custody',`${user}.json`)));
  assert.equal(state.status,'deleting');assert.equal(state.sessions[0],id);
  f.entry={sessionId:randomUUID()};const unrelated=f.transcript();
  await assert.rejects(f.module.purge(user),/unavailable/);assert.equal(fs.existsSync(unrelated),true);
  f.entry=null;f.lostReply=false;
  const restarted=f.restart();
  assert.equal((await restarted.purge(user)).deleted,true);
  assert.equal(fs.readdirSync(f.directory).some(name=>name.startsWith(id)),false);
  assert.equal(fs.existsSync(unrelated),true);
});
test('rotation between hooks uses registered ID and verified header, never a filename alone',async t=>{
  const f=setup(t);await f.module.bind(user);const old=f.entry.sessionId;
  const original=f.transcript();fs.renameSync(original,original+'.reset.2026-09-30');
  f.entry={sessionId:randomUUID()};await f.module.bind(user);f.transcript();
  const deceptive=path.join(f.directory,`${old}.jsonl.deleted.forged`);
  fs.writeFileSync(deceptive,JSON.stringify({type:'session',id:randomUUID()})+'\n',{mode:0o600});
  await assert.rejects(f.module.purge(user),/unavailable/);
  assert.equal(fs.existsSync(deceptive),true);
  assert.equal(JSON.parse(fs.readFileSync(path.join(f.directory,'.ods-image-custody',`${user}.json`))).status,'deleting');
});
test('active maintenance, custom paths, symlinks and foreign hook identities fail closed',async t=>{
  const f=setup(t);await f.module.bind(user);const original=f.transcript();f.busy=true;
  await assert.rejects(f.module.purge(user),/busy/);assert.equal(fs.existsSync(original),true);assert.equal(f.calls.length,0);
  f.busy=false;f.entry={...f.entry,sessionFile:path.join(f.directory,'custom.jsonl')};
  await assert.rejects(f.module.purge(user),/unavailable/);assert.equal(f.calls.length,0);
  delete f.entry.sessionFile;
  assert.throws(()=>f.module.observe({sessionKey:`agent:pixel:openai-user:${user}`,sessionId:randomUUID()}),/unavailable/);
  if(process.platform!=='win32') {fs.unlinkSync(original);fs.symlinkSync('/etc/hosts',original);await assert.rejects(f.module.purge(user),/unavailable/);assert.equal(fs.lstatSync(original).isSymbolicLink(),true);}
});
test('plain text hook does not create custody files or require an existing session store',t=>{
  const f=setup(t);f.module.observe({sessionKey:`agent:pixel:openai-user:${user}`,sessionId:randomUUID()});
  assert.deepEqual(fs.readdirSync(f.directory),[]);
});
test('image cache deletion is scoped, durable, idempotent and blocks stale writes while reclaiming quota',t=>{
  const f=setup(t), directory=path.join(f.directory,'images'), store=createChatImageStore(directory);
  const data=Buffer.from([137,80,78,71,13,10,26,10,1]);const image={id:`img-${'1'.repeat(32)}`,sha256:createHash('sha256').update(data).digest('hex'),mimeType:'image/png',data};
  const ref={id:image.id,sha256:image.sha256};store.put(user,[image],[ref]);store.put(other,[image],[ref]);
  assert.equal(store.deleteConversation(user).deleted,true);
  const restarted=createChatImageStore(directory);assert.throws(()=>restarted.put(user,[image],[ref]),/conversation-deleted/);
  assert.throws(()=>restarted.read(user,ref,[ref]),/conversation-deleted/);
  assert.equal(restarted.read(other,ref,[ref]).data.equals(data),true);
  assert.equal(restarted.deleteConversation(user).deleted,true);
});
test('ledger requires its lock, refuses unresolved runs, tombstones prevent old history and completion resurrection',t=>{
  const f=setup(t),ledger=createChatHistoryLedger(path.join(f.directory,'ledger'));
  const snapshot={schemaVersion:1,messages:[{role:'user',content:'hello'}]},native={status:'missing',sessionRevision:null,compaction:{count:0}};
  assert.throws(()=>ledger.deleteConversation(user),/lock-required/);
  const release=ledger.lock(user);const prepared=ledger.prepare(user,'attempt',snapshot,native);
  assert.throws(()=>ledger.deleteConversation(user),/outcome-unknown/);ledger.abandon(user,prepared);
  ledger.deleteConversation(user);release();
  const restarted=createChatHistoryLedger(path.join(f.directory,'ledger'));
  assert.throws(()=>restarted.prepare(user,'new-attempt',snapshot,native),/conversation-deleted/);
  assert.throws(()=>restarted.search(user),/conversation-deleted/);
  assert.equal(restarted.complete(user,prepared,{},{}),null);assert.equal(restarted.read(user).status,'deleted');
});
