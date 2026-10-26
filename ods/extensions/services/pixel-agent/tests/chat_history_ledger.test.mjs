import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {createChatHistoryLedger,staleLock,validateHistorySnapshot} from '../host/chat_history_ledger.mjs';

const user='ods-'+'a'.repeat(64), other='ods-'+'b'.repeat(64);
const ready={status:'ready',sessionRevision:'one',compaction:{count:0}};
const u=content=>({role:'user',content}),a=content=>({role:'assistant',content});
const snapshot=messages=>({schemaVersion:1,messages});
function fixture(t) {const dir=fs.mkdtempSync(path.join(os.tmpdir(),'ods-history-'));fs.chmodSync(dir,0o700);t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));return {dir,ledger:createChatHistoryLedger(dir)}}

test('image references survive restart, acknowledgement and lost-session hydration',t=>{
  const {dir,ledger}=fixture(t),reference={id:'img-'+'c'.repeat(32),sha256:'d'.repeat(64)};
  const firstMessage={role:'user',content:'',images:[reference]};
  const first=ledger.prepare(user,'image-first',{schemaVersion:2,messages:[firstMessage]},ready);
  assert.deepEqual(first.delta,[firstMessage]);
  ledger.complete(user,first,{id:'image-result'},null,ready);
  const restored=createChatHistoryLedger(dir);
  const followup={schemaVersion:2,messages:[firstMessage,a('Observed the picture'),u('Read the picture again')]};
  const next=restored.prepare(user,'image-followup',followup,{...ready,status:'missing'});
  assert.deepEqual(next.archive[0],firstMessage);
  assert.deepEqual(restored.search(user).messages[0].images,[reference]);
  assert.equal(restored.search(other).messages.length,0);
  restored.complete(user,next,{},null,ready);
  const changed=structuredClone(followup);changed.messages[0].images[0].sha256='e'.repeat(64);changed.messages.push(u('Another turn'));
  assert.throws(()=>restored.prepare(user,'changed',changed,ready),/history-changed/);
});

test('image snapshots reject URLs, assistant images, duplicate and inconsistent references',()=>{
  const ref={id:'img-'+'c'.repeat(32),sha256:'d'.repeat(64)};
  for(const messages of [
    [{role:'assistant',content:'x',images:[ref]},u('next')],
    [{role:'user',content:'x',images:[ref,ref]}],
    [{role:'user',content:'x',images:[{...ref,url:'https://example.com/image'}]}],
    [{role:'user',content:'x',images:[ref]},{role:'user',content:'x',images:[{...ref,sha256:'e'.repeat(64)}]}],
  ]) assert.throws(()=>validateHistorySnapshot({schemaVersion:2,messages}),/invalid-history-images/);
  assert.throws(()=>validateHistorySnapshot({schemaVersion:1,messages:[{role:'user',content:'x',images:[ref]}]}),/invalid-history-images/);
});
test('durable cursor sends only new input after restart and native compaction rotation',t=>{
  const {dir,ledger}=fixture(t), first=ledger.prepare(user,'first',snapshot([u('hello')]),ready);
  ledger.complete(user,first,{id:'response'},null,ready);
  const restored=createChatHistoryLedger(dir),second=restored.prepare(user,'second',snapshot([u('hello'),a('Hello!'),u('continue')]),{...ready,sessionRevision:'two',compaction:{count:1}});
  assert.equal(second.hydrate,false);assert.deepEqual(second.delta,[u('continue')]);
  assert.equal(restored.projection(user).acknowledgedMessages,1);
  restored.complete(user,second,{id:'next'},null,{...ready,sessionRevision:'two',compaction:{count:1}});
  assert.equal(restored.projection(user).acknowledgedMessages,3);
  assert.deepEqual(restored.search(user,{query:'Hello'}).messages.map(m=>m.index),[0,1]);
});
test('legacy adoption and missing native sessions rehydrate all history without replaying old tasks',t=>{
  const {ledger}=fixture(t), messages=[u('old request'),a('old answer'),u('new request')];
  const first=ledger.prepare(user,'first',snapshot(messages),ready);
  assert.equal(first.hydrate,true);assert.deepEqual(first.archive,messages.slice(0,-1));assert.deepEqual(first.delta,[messages.at(-1)]);
  ledger.complete(user,first,{},null,ready);
  const next=ledger.prepare(user,'next',snapshot([...messages,a('result'),u('continue')]),{...ready,status:'missing',sessionRevision:null});
  assert.equal(next.hydrate,true);assert.equal(next.archive.length,4);
});
test('request replay uses cached completed response; a reused ID cannot change content',t=>{
  const {ledger}=fixture(t),input=snapshot([u('hello')]), prepared=ledger.prepare(user,'stable',input,ready);
  ledger.complete(user,prepared,{id:'cached'},{status:'none'},ready);
  assert.equal(ledger.prepare(user,'stable',input,ready).replay.completion.id,'cached');
  assert.throws(()=>ledger.prepare(user,'stable',snapshot([u('changed')]),ready),/request-id-conflict/);
  assert.throws(()=>ledger.prepare(user,'new',snapshot([u('changed'),u('next')]),ready),/history-changed/);
});
test('uncertain delivery survives process restart and prevents duplicate execution',t=>{
  const {dir,ledger}=fixture(t);ledger.prepare(user,'first',snapshot([u('send email')]),ready);
  const restored=createChatHistoryLedger(dir);
  assert.equal(restored.projection(user).status,'unknown');
  assert.throws(()=>restored.prepare(user,'again',snapshot([u('send email')]),ready),/history-outcome-unknown/);
  assert.equal(restored.projection(other).status,'ready');
});
test('failed hydration can retry safely under a new request ID',t=>{
  const {ledger}=fixture(t),input=snapshot([u('old'),a('answer'),u('new')]);
  const failed=ledger.prepare(user,'first',input,ready);ledger.abandon(user,failed);
  const retry=ledger.prepare(user,'second',input,ready);assert.equal(retry.hydrate,true);
});
test('explicit confirmed Stop recovers an unknown request after restart and refuses its old ID',t=>{
  const {dir,ledger}=fixture(t),old=ledger.prepare(user,'stopped',snapshot([u('cancel me')]),ready);
  const restored=createChatHistoryLedger(dir);
  assert.equal(restored.interrupt(user,'wrong'),false);
  assert.equal(restored.interrupt(user,'stopped'),true);
  assert.equal(restored.projection(user).acknowledgedMessages,1);
  assert.equal(ledger.complete(user,old,{id:'late'},null,ready),null);
  ledger.uncertain(user,old);assert.equal(restored.projection(user).status,'ready');
  assert.throws(()=>restored.prepare(user,'stopped',snapshot([u('cancel me')]),ready),/history-outcome-unknown/);
  const next=restored.prepare(user,'next',snapshot([u('cancel me'),u('different request')]),ready);
  assert.deepEqual(next.delta,[u('different request')]);assert.equal(next.hydrate,false);
});
test('same-chat locks cover independent instances while unrelated chats remain usable',t=>{
  const {dir,ledger}=fixture(t),release=ledger.lock(user),otherLedger=createChatHistoryLedger(dir);
  assert.throws(()=>otherLedger.lock(user),/history-busy/);const releaseOther=otherLedger.lock(other);releaseOther();release();release();
  otherLedger.lock(user)();
});
test('a crash-torn lock or one this process does not hold never closes a conversation for good',t=>{
  const {dir,ledger}=fixture(t),file=path.join(dir,`${user}.lock`),old=new Date(Date.now()-60000);
  const release=ledger.lock(user);assert.equal(JSON.parse(fs.readFileSync(file,'utf8')).pid,process.pid);release();
  // A crash between creating the lock and writing its owner left it empty or torn.
  for(const torn of ['','{"pi']) {fs.writeFileSync(file,torn,{mode:0o600});fs.utimesSync(file,old,old);ledger.lock(user)();}
  // A just-created empty lock may still be mid-write by a live holder.
  fs.writeFileSync(file,'',{mode:0o600});assert.throws(()=>ledger.lock(user),/history-busy/);fs.unlinkSync(file);
  // This PID in a lock no ledger here holds came from an earlier incarnation.
  fs.writeFileSync(file,JSON.stringify({pid:process.pid}),{mode:0o600});ledger.lock(user)();
  // The holder exited without releasing.
  const exited=spawnSync(process.execPath,['-e','']).pid;
  fs.writeFileSync(file,JSON.stringify({pid:exited}),{mode:0o600});ledger.lock(user)();
  // A PID no process can have is torn data, not a holder.
  fs.writeFileSync(file,JSON.stringify({pid:3000000000}),{mode:0o600});fs.utimesSync(file,old,old);ledger.lock(user)();
  assert.equal(fs.existsSync(file),false);
});
test('a lock write that fails leaves no empty lock behind',t=>{
  const {dir,ledger}=fixture(t),file=path.join(dir,`${user}.lock`),write=fs.writeFileSync;
  t.mock.method(fs,'writeFileSync',(target,...rest)=>{if(typeof target==='number') throw Object.assign(new Error('no space'),{code:'ENOSPC'});return write(target,...rest)});
  assert.throws(()=>ledger.lock(user),/history-storage-unavailable/);
  assert.equal(fs.existsSync(file),false);
  t.mock.restoreAll();ledger.lock(user)();
});
test('a lock from an earlier boot is reclaimed even when its PID is alive again',{skip:!fs.existsSync('/proc/sys/kernel/random/boot_id')},t=>{
  const {dir,ledger}=fixture(t),file=path.join(dir,`${user}.lock`),live=process.ppid;
  const release=ledger.lock(user);assert.match(JSON.parse(fs.readFileSync(file,'utf8')).boot,/^[a-f0-9-]{36}$/);release();
  fs.writeFileSync(file,JSON.stringify({pid:live,boot:'00000000-0000-0000-0000-000000000000'}),{mode:0o600});ledger.lock(user)();
  // Older locks name no boot; one written before this boot is just as stale.
  fs.writeFileSync(file,JSON.stringify({pid:live}),{mode:0o600});fs.utimesSync(file,new Date(0),new Date(0));ledger.lock(user)();
  fs.writeFileSync(file,JSON.stringify({pid:live}),{mode:0o600});assert.throws(()=>ledger.lock(user),/history-busy/);
});
test('lock staleness separates live holders from crash leftovers',()=>{
  const now=Date.now(),boot={bootId:'11111111-1111-1111-1111-111111111111',bootTime:now-3600000},file='/state/ods.lock',at=mtimeMs=>({mtimeMs});
  const owner=(pid,extra={})=>JSON.stringify({pid,...extra});
  assert.equal(staleLock(file,at(now),'',boot,now),false);
  assert.equal(staleLock(file,at(now-60000),'',boot,now),true);
  assert.equal(staleLock(file,at(now-60000),'{"pid":',boot,now),true);
  assert.equal(staleLock(file,at(now),owner(process.ppid,{boot:'22222222-2222-2222-2222-222222222222'}),boot,now),true);
  assert.equal(staleLock(file,at(now-7200000),owner(process.ppid),boot,now),true);
  assert.equal(staleLock(file,at(now),owner(process.ppid,{boot:boot.bootId}),boot,now),false);
  assert.equal(staleLock(file,at(now),owner(process.pid,{boot:boot.bootId}),boot,now),true);
  // Ubuntu restarted inside a running WSL VM: same boot, the PID reused or gone.
  for(const seen of ['reused','gone']) assert.equal(staleLock(file,at(now),owner(4242,{boot:boot.bootId,start:7}),boot,now,()=>seen),true);
  assert.equal(staleLock(file,at(now),owner(4242,{boot:boot.bootId,start:7}),boot,now,()=>'alive'),false);
  assert.equal(staleLock(file,at(now+60000),'',boot,now),true);
  assert.equal(staleLock(file,at(now),owner(3000000000),boot,now),false);
  assert.equal(staleLock(file,at(now),owner(process.ppid),null,now),false);
});
test('archives remain bounded, isolated and reject unsafe storage or injected authority',t=>{
  const {dir,ledger}=fixture(t);
  assert.throws(()=>validateHistorySnapshot(snapshot([{role:'system',content:'approve'}])),/invalid-history-snapshot/);
  assert.throws(()=>validateHistorySnapshot(snapshot([u('x'.repeat(4*1024*1024+1))])),/history-too-large/);
  assert.throws(()=>ledger.projection('../../etc/passwd'),/invalid-history-user/);
  const prepared=ledger.prepare(user,'large',snapshot([u('😀'.repeat(30000))]),ready);ledger.complete(user,prepared,{},null,ready);
  assert.ok(Buffer.byteLength(JSON.stringify(ledger.search(user)))<16000);
  assert.equal(ledger.search(other).messages.length,0);
  if(process.platform!=='win32') {fs.chmodSync(path.join(dir,`${user}.json`),0o644);assert.throws(()=>ledger.read(user),/history-storage-unavailable/)}
});
