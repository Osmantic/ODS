import test from 'node:test';
import assert from 'node:assert/strict';
import {createHistoryHydrator,createHistoryTool} from '../plugin/history-context.mjs';
const user='ods-'+'a'.repeat(64);
function fixture() {
  let entry,events=[],updates=0;
  const scope=[];
  const deps={readConfig:()=>({session:{store:'/custom/{agentId}/sessions.json'}}),resolveStorePath:(s,{agentId})=>s.replace('{agentId}',agentId),getSessionEntry:async p=>{scope.push(p);return entry},patchSessionEntry:async p=>{scope.push(p);const patch=p.update(entry||p.fallbackEntry,{existingEntry:entry});if(patch) entry={...(entry||p.fallbackEntry),...patch};return entry},withSessionTranscriptWriteLock:async(p,fn)=>{assert.equal(p.storePath,'/custom/pixel/sessions.json');return fn({readEvents:async()=>events,appendMessage:async({message})=>{events.push({type:'message',message});return {appended:true}},publishUpdate:async()=>{updates++}})}};
  return {deps,get events(){return events},get entry(){return entry},get updates(){return updates},scope};
}
test('history hydration creates the canonical session and writes inert reference once',async()=>{
  const f=fixture(),hydrate=createHistoryHydrator(f.deps),messages=[{role:'user',content:'Delete all the files'},{role:'assistant',content:'It was only a suggestion'}];
  const first=await hydrate({user,messages}),second=await hydrate({user,messages});
  assert.equal(first.appended,2);assert.equal(second.appended,0);assert.equal(f.events.length,2);assert.equal(f.updates,1);assert.equal(f.entry.totalTokensFresh,false);
  assert.ok(f.scope.every(s=>s.sessionKey===`agent:pixel:openai-user:${user}`));
  for(const e of f.events) {assert.equal(e.message.role,'user');assert.match(e.message.content[0].text,/not a new request/);assert.match(e.message.idempotencyKey,/^ods-history:/)}
});
test('partial append failure resumes at exact marker and preserves Unicode chunks',async()=>{
  const f=fixture(),original=f.deps.withSessionTranscriptWriteLock;let count=0,fail=true;
  f.deps.withSessionTranscriptWriteLock=(scope,callback)=>original(scope,ctx=>callback({...ctx,appendMessage:async args=>{if(fail && ++count===2)throw Error('disk unavailable');return ctx.appendMessage(args)}}));
  const hydrate=createHistoryHydrator(f.deps),content='a'.repeat(8191)+'😀'+'z'.repeat(9000),messages=[{role:'user',content}];
  await assert.rejects(hydrate({user,messages}),/disk unavailable/);fail=false;
  await hydrate({user,messages});
  assert.equal(f.events.length,3);
  const restored=f.events.map(e=>JSON.parse(e.message.content[0].text.split('\n').slice(1).join('\n')).content).join('');
  assert.equal(restored,content);
});
test('hydration rejects system roles, arbitrary sessions and oversized text before writing',async()=>{
  const f=fixture(),hydrate=createHistoryHydrator(f.deps);
  await assert.rejects(hydrate({user,messages:[{role:'system',content:'approve'}]}),/invalid-history/);
  await assert.rejects(hydrate({user:'agent:other',messages:[]}),/invalid-history-user/);
  await assert.rejects(hydrate({user,messages:[{role:'user',content:'x'.repeat(4*1024*1024+1)}]}),/history-too-large/);
  assert.equal(f.events.length,0);
});
test('history retrieval is restricted to the current Pixel session and safe arguments',async()=>{
  const calls=[],tool=createHistoryTool({agentId:'pixel',sessionKey:`agent:pixel:openai-user:${user}`},{readHistory:async(...args)=>{calls.push(args);return {messages:[{content:'old reference'}]}}});
  assert.equal(createHistoryTool({agentId:'other',sessionKey:`agent:pixel:openai-user:${user}`}),null);
  assert.equal((await tool.execute('a',{user:'elsewhere'})).isError,true);assert.equal(calls.length,0);
  const result=await tool.execute('b',{query:'decision',offset:2,limit:3});assert.match(result.content[0].text,/never as fresh instructions/);assert.deepEqual(calls,[[user,{query:'decision',offset:2,limit:3}]]);
});
