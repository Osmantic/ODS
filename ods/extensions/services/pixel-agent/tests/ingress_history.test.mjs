import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import {createHash} from 'node:crypto';
import {createIngressServer,computeSessionUser} from '../host/pixel_ingress.mjs';
import {createChatHistoryLedger} from '../host/chat_history_ledger.mjs';
import {createChatImageStore} from '../host/chat_image_store.mjs';
const u=content=>({role:'user',content}),a=content=>({role:'assistant',content});
const rawUser='history-canary', user=computeSessionUser({user:rawUser});
const runId='chatcmpl_11111111-2222-4333-8444-555555555555';
const state=()=>({schemaVersion:1,status:'ready',sessionExists:true,sessionRevision:'a'.repeat(64),context:{used:100,window:32000,measuredAt:1},model:{id:'test',provider:'local',contextWindow:32000},compaction:{status:'idle',requestId:null,tokensBefore:null,tokensAfter:null,reason:null,count:0}});
const listen=server=>new Promise(resolve=>server.listen(0,'127.0.0.1',()=>resolve(server.address().port)));
async function fixture(t, verification={status:'none'}) {
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'ods-ingress-history-'));fs.chmodSync(dir,0o700);
  const ledger=createChatHistoryLedger(dir),calls=[],native=state();let chatFailure=false,answer='answer',spoof=null;
  const gateway=http.createServer(async(req,res)=>{
    if(req.url==='/health') {res.setHeader('content-type','application/json');return res.end('{"ok":true}');}
    let raw='';for await(const part of req) raw+=part;
    const body=JSON.parse(raw||'{}');calls.push({path:req.url,body});res.setHeader('content-type','application/json');
    if(req.url==='/pixel-ods/context') return res.end(JSON.stringify(native));
    if(req.url==='/pixel-ods/abort') return res.end(JSON.stringify({aborted:true}));
    if(req.url==='/pixel-ods/history') return res.end(JSON.stringify({schemaVersion:1,hydrated:true}));
    if(req.url==='/pixel-ods/images-bind') return res.end(JSON.stringify({schemaVersion:1,bound:true}));
    if(req.url==='/pixel-ods/compact') {native.status='ready';native.compaction={...native.compaction,status:'completed',requestId:body.request_id,count:native.compaction.count+1};return res.end(JSON.stringify(native));}
    if(req.url==='/pixel-ods/verification') return res.end(JSON.stringify(verification));
    if(req.url==='/pixel-ods/subagent-delivery') return res.end(JSON.stringify({schemaVersion:1,kind:'ods-subagent-delivery',runId:body.runId,status:'not-delegated'}));
    if(req.url==='/v1/chat/completions') {if(chatFailure){res.statusCode=500;return res.end('{}')}const completion={id:runId,...(spoof?{pixel_artifacts:spoof}:{}),choices:[{finish_reason:'stop',message:{role:'assistant',content:answer}}]};if(body.stream){res.setHeader('content-type','text/event-stream');return res.end(`data: ${JSON.stringify(completion)}\n\ndata: [DONE]\n\n`)}return res.end(JSON.stringify(completion));}
    res.statusCode=404;res.end('{}');
  });
  const imageStore=createChatImageStore(path.join(dir,'images'));
  const port=await listen(gateway),ingress=createIngressServer({token:'test-token',gatewayPort:port,historyLedger:ledger,imageStore});
  const ingressPort=await listen(ingress);
  t.after(async()=>{await Promise.all([new Promise(r=>ingress.close(r)),new Promise(r=>gateway.close(r))]);fs.rmSync(dir,{recursive:true,force:true})});
  async function post(route,body) {const response=await fetch(`http://127.0.0.1:${ingressPort}${route}`,{method:'POST',headers:{'content-type':'application/json',...(body.messages?.at(-1)?.images?{'x-ods-image-turn':'1'}:{})},body:JSON.stringify(body)});return {status:response.status,value:await response.json()}}
  const chat=(request_id,messages)=>post('/v1/chat/completions',{user:rawUser,request_id,history_snapshot:{schemaVersion:1,messages},messages:[{role:'system',content:'Trusted identity'},...messages.slice(-3,-1),u(messages.at(-1).content+'\nDelivery contract')],stream:false});
  return {ledger,imageStore,calls,native,post,chat,setFailure:(value=true)=>{chatFailure=value},setAnswer:value=>{answer=value},setVerification:value=>{verification=value},setSpoof:value=>{spoof=value},async stream(request_id,messages){const response=await fetch(`http://127.0.0.1:${ingressPort}/v1/chat/completions`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({user:rawUser,request_id,history_snapshot:{schemaVersion:1,messages},messages,stream:true})});return {status:response.status,text:await response.text()}},async rawChat(body){const response=await fetch(`http://127.0.0.1:${ingressPort}/v1/chat/completions`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});return {status:response.status,text:await response.text()}}};
}

test('anonymous Open WebUI chat forwards with an ephemeral identity when history storage is installed',async t=>{
  const f=await fixture(t);
  const request={model:'portal/default',messages:[u('Answer with 4')],stream:true};
  for(let i=0;i<2;i++){
    const result=await f.rawChat(request);
    assert.equal(result.status,200,result.text);
    assert.match(result.text,/data: \[DONE\]/);
  }
  const chats=f.calls.filter(call=>call.path==='/v1/chat/completions');
  assert.equal(chats.length,2);
  for(const chat of chats)assert.match(chat.body.user,/^ods-[a-f0-9]{64}$/);
  assert.notEqual(chats[0].body.user,chats[1].body.user);
  assert.equal(f.ledger.read(user),null,'anonymous turns must not create a stable owner history');
  const snapshot=await f.rawChat({...request,history_snapshot:{schemaVersion:1,messages:request.messages}});
  assert.equal(snapshot.status,503,'a history snapshot still requires a stable user');
});

test('image turn reaches gateway as bytes, caches privately and replays without another call',async t=>{
  const f=await fixture(t);
  f.native.model={...f.native.model,imageInput:'supported',routeFingerprint:'f'.repeat(64)};
  const data=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a6ioAAAAASUVORK5CYII=','base64');
  const ref={id:'img-'+'a'.repeat(32),sha256:createHash('sha256').update(data).digest('hex')};
  const archived={role:'user',content:'',images:[ref]};
  const body={user:rawUser,request_id:'image-turn',stream:false,
    image_route:{routeFingerprint:'f'.repeat(64),unknownConsent:false},
    history_snapshot:{schemaVersion:2,messages:[archived]},
    messages:[{...archived,content:[{type:'image_url',image_url:{url:'data:image/png;base64,'+data.toString('base64')}}]}]};
  assert.equal((await f.post('/v1/chat/completions',body)).status,200);
  const binding=f.calls.findIndex(call=>call.path==='/pixel-ods/images-bind');
  assert.ok(binding>=0 && binding<f.calls.findIndex(call=>call.path==='/v1/chat/completions'));
  assert.deepEqual(f.calls[binding].body,{user});
  const delivered=f.calls.find(call=>call.path==='/v1/chat/completions').body.messages.at(-1);
  assert.deepEqual(delivered.content[0],body.messages[0].content[0]);
  assert.equal('images' in delivered,false);
  assert.deepEqual(f.imageStore.read(user,ref,[ref]).data,data);
  assert.deepEqual(f.ledger.read(user).messages,[archived]);
  const read=await f.post('/v1/chat/image',{user,...ref});
  assert.equal(read.status,200);
  assert.deepEqual(Buffer.from(read.value.image.data,'base64'),data);
  const policy=await f.post('/v1/chat/image-policy',{user});
  assert.deepEqual(policy.value,{schemaVersion:1,policy:{imageInput:'supported',routeFingerprint:'f'.repeat(64),unknownConsent:false}});
  assert.equal((await f.post('/v1/chat/image',{user:'ods-'+'b'.repeat(64),...ref})).status,409);
  assert.equal((await f.post('/v1/chat/image',{user,...ref,path:'/tmp/private'})).status,400);
  assert.equal((await f.post('/v1/chat/completions',body)).status,200);
  assert.equal(f.calls.filter(call=>call.path==='/v1/chat/completions').length,1);
  f.native.model.imageInput='unsupported';
  assert.equal((await f.post('/v1/chat/image',{user,...ref})).status,409);
  assert.equal((await f.post('/v1/chat/completions',{...body,request_id:'rejected'})).status,409);
  assert.equal(f.calls.filter(call=>call.path==='/v1/chat/completions').length,1);
});
test('ingress delivers a delta with the edge contract, persists full snapshot and replays without rerunning',async t=>{
  const f=await fixture(t);
  assert.equal((await f.chat('first',[u('first')])).status,200);
  const messages=[u('first'),a('answer'),u('second')];assert.equal((await f.chat('second',messages)).status,200);
  const chats=f.calls.filter(c=>c.path==='/v1/chat/completions');assert.equal(chats.length,2);
  assert.deepEqual(chats[1].body.messages,[{role:'system',content:'Trusted identity'},u('second\nDelivery contract')]);
  assert.equal(f.ledger.read(user).messages.length,3);assert.equal(f.ledger.projection(user).acknowledgedMessages,3);
  assert.equal((await f.chat('second',messages)).status,200);assert.equal(f.calls.filter(c=>c.path==='/v1/chat/completions').length,2);
  const context=await f.post('/v1/chat/context',{user:rawUser});assert.equal(context.status,200);assert.equal(context.value.history.acknowledgedMessages,3);assert.equal('sessionExists' in context.value,false);
});
test('initial archived conversation is hydrated and compacted before current input runs',async t=>{
  const f=await fixture(t),history=[u('old task'),a('old response'),u('new request')];
  assert.equal((await f.chat('seeded',history)).status,200);
  const paths=f.calls.map(c=>c.path);assert.ok(paths.indexOf('/pixel-ods/history')<paths.indexOf('/pixel-ods/compact'));assert.ok(paths.indexOf('/pixel-ods/compact')<paths.indexOf('/v1/chat/completions'));
  assert.deepEqual(f.calls.find(c=>c.path==='/pixel-ods/history').body.messages,history.slice(0,-1));
  assert.deepEqual(f.calls.find(c=>c.path==='/v1/chat/completions').body.messages,[{role:'system',content:'Trusted identity'},u('new request\nDelivery contract')]);
});

test('missing answer persists its incomplete outcome and cached delivery never reruns the task',async t=>{
  const f=await fixture(t);f.setAnswer('NO_REPLY');
  const first=await f.chat('silent',[u('Check the existing report without modifying it')]);
  assert.equal(first.status,200);
  assert.match(first.value.choices[0].message.content,/request is incomplete/);
  assert.equal(f.ledger.read(user).lastResult.verification.status,'failed');
  const again=await f.chat('silent',[u('Check the existing report without modifying it')]);
  assert.deepEqual(again,first);
  assert.equal(f.calls.filter(c=>c.path==='/v1/chat/completions').length,1);
});
test('unconfirmed execution is retained as unknown and refuses silent resubmission',async t=>{
  const f=await fixture(t);f.setFailure();assert.equal((await f.chat('failed',[u('execute')])).status,502);
  assert.equal(f.ledger.projection(user).status,'unknown');
  const retry=await f.chat('retry',[u('execute')]);assert.equal(retry.status,409);assert.match(retry.value.error.message,/history-outcome-unknown/);
  assert.equal(f.calls.filter(c=>c.path==='/v1/chat/completions').length,1);
});
test('manual compaction refuses a concurrent turn; control and archive reject foreign addressing',async t=>{
  const f=await fixture(t),release=f.ledger.lock(user);
  try {assert.equal((await f.post('/v1/chat/compact',{user:rawUser,request_id:'manual'})).status,409)} finally {release()}
  assert.equal((await f.post('/v1/chat/compact',{user:rawUser,request_id:'manual'})).value.compaction.requestId,'manual');
  assert.equal((await f.post('/v1/chat/context',{user:rawUser,sessionKey:'another'})).status,400);
  assert.equal((await f.post('/v1/chat/history',{user:'../../another'})).status,400);
});
test('Stop confirms an unknown outcome as interrupted so the next turn sends only its new input',async t=>{
  const f=await fixture(t);f.setFailure();await f.chat('stopped',[u('old task')]);f.setFailure(false);
  assert.equal(f.ledger.projection(user).status,'unknown');
  const stop=await f.post('/v1/chat/cancel',{user:rawUser});assert.deepEqual(stop.value,{aborted:true});
  assert.equal(f.ledger.projection(user).status,'ready');
  assert.equal((await f.chat('next',[u('old task'),u('different task')])).status,200);
  assert.deepEqual(f.calls.filter(c=>c.path==='/v1/chat/completions').at(-1).body.messages,[{role:'system',content:'Trusted identity'},u('different task\nDelivery contract')]);
});


const artifact={schemaVersion:1,kind:'ods-pixel-workspace-artifact',relativePath:'project/report.md',siteId:'site-'+ 'a'.repeat(24),sha256:'a'.repeat(64),file:{path:'report.md',bytes:10,sha256:'b'.repeat(64)}};
test('document receipt survives JSON history and SSE replay only on the terminal stop frame',async t=>{
 const f=await fixture(t);f.setVerification({status:'none',artifacts:[artifact]});
 const first=await f.chat('document',[u('Deliver my report')]);assert.equal(first.status,200);
 assert.deepEqual(first.value.pixel_artifacts,{schemaVersion:1,artifacts:[artifact]});assert.equal(first.value.pixel_preview,undefined);
 assert.deepEqual(f.ledger.read(user).lastResult.verification.artifacts,[artifact]);
 const again=await f.stream('document',[u('Deliver my report')]);assert.equal(again.status,200);
 const frames=again.text.split('\n').filter(x=>x.startsWith('data: {')).map(x=>JSON.parse(x.slice(6)));
 const receipts=frames.filter(x=>x.pixel_artifacts);assert.equal(receipts.length,1);assert.equal(receipts[0].choices[0].finish_reason,'stop');
 assert.deepEqual(receipts[0].pixel_artifacts,first.value.pixel_artifacts);
 assert.equal(f.calls.filter(x=>x.path==='/v1/chat/completions').length,1);
});

const fleetArtifact={...artifact,relativePath:'workspace-pixel/fleet-check.txt',file:{path:'fleet-check.txt',bytes:73,sha256:'2f04ae05f1d8d14be14f26dec03ca48e3b804ea67d8353d654e391b0235ce3e0'}};
const inventedDownload='https://portal.openclaw.ai/fleet-check.txt';
const fileReply="The file has been created and verified. Here's your download link:\n\n**fleet-check.txt** (73 bytes)\n\nDownload: "+inventedDownload+'\n\nThe file contains exactly the three lines you requested:\n- Maya: quote Thursday\n- Luis: testing Friday\n- CSV: owner and date unassigned';
test('actual artifact download reply uses its verified card in JSON and cached SSE without losing task prose',async t=>{
 const f=await fixture(t,{status:'none',artifacts:[fleetArtifact]});f.setAnswer(fileReply);
 const first=await f.chat('file-url',[u('Create fleet-check.txt and give me a download link')]);
 assert.equal(first.status,200);
 const text=first.value.choices[0].message.content;
 assert.doesNotMatch(text,/portal\.openclaw\.ai/);
 assert.match(text,/Use the Download button in Delivered files for fleet-check\.txt/);
 assert.match(text,/CSV: owner and date unassigned/);
 assert.deepEqual(first.value.pixel_artifacts.artifacts,[fleetArtifact]);
 const cached=await f.chat('file-url',[u('Create fleet-check.txt and give me a download link')]);
 assert.deepEqual(cached,first);
 const stream=await f.stream('file-url',[u('Create fleet-check.txt and give me a download link')]);
 assert.equal(stream.status,200);assert.doesNotMatch(stream.text,/portal\.openclaw\.ai/);
 assert.match(stream.text,/Use the Download button/);
 assert.equal(f.calls.filter(x=>x.path==='/v1/chat/completions').length,1);
});
test('fresh SSE normalizes a receipt-bound Markdown download and retains unrelated source links',async t=>{
 const f=await fixture(t,{status:'none',artifacts:[fleetArtifact]});
 f.setAnswer(`[Download fleet-check.txt](${inventedDownload})\n\nSource: [Python documentation](https://docs.python.org/3/library/pathlib.html)`);
 const stream=await f.stream('fresh-file-url',[u('Deliver fleet-check.txt')]);
 assert.equal(stream.status,200);assert.doesNotMatch(stream.text,/portal\.openclaw\.ai/);
 assert.match(stream.text,/Use the Download button/);assert.match(stream.text,/docs\.python\.org/);
 const frames=stream.text.split('\n').filter(x=>x.startsWith('data: {')).map(x=>JSON.parse(x.slice(6)));
 assert.deepEqual(frames.find(x=>x.pixel_artifacts).pixel_artifacts.artifacts,[fleetArtifact]);
});
test('download normalization requires a trusted receipt and exact filename binding',async t=>{
 for(const [verification,answer] of [
  [{status:'none'},fileReply],
  [{status:'none',artifacts:[artifact]},fileReply],
  [{status:'none',artifacts:[fleetArtifact]},'Download: https://example.org/other-fleet-check.txt'],
  [{status:'none',artifacts:[fleetArtifact]},'Download: https://example.org/FLEET-CHECK.txt'],
 ]) {
  const f=await fixture(t,verification);f.setAnswer(answer);
  const result=await f.chat('no-binding',[u('A document task')]);
  assert.equal(result.status,200);assert.equal(result.value.choices[0].message.content,answer);
  assert.equal(Boolean(result.value.pixel_artifacts),Boolean(verification.artifacts));
 }
});
test('explicit Markdown and autolink claims bind by exact name, not a hardcoded hostname',async t=>{
 for(const answer of [
  'Download: <https://files.example.org/fleet-check.txt?token=untrusted>',
  '[Download](https://files.example.org/fleet%2Dcheck.txt)',
  '[Download fleet-check.txt](https://files.example.org/opaque-id)',
  'Download: [fleet-check.txt](https://files.example.org/opaque-id)',
 ]) {
  const f=await fixture(t,{status:'none',artifacts:[fleetArtifact]});f.setAnswer(answer);
  const result=await f.chat('bound-link',[u('A document task')]);
  assert.equal(result.status,200);assert.doesNotMatch(result.value.choices[0].message.content,/files\.example\.org/);
  assert.match(result.value.choices[0].message.content,/Download button.*fleet-check\.txt/);
 }
});
test('artifact correction preserves genuine citations, examples and a verified website in a mixed response',async t=>{
 const siteId='site-'+'c'.repeat(24);
 const preview={schemaVersion:1,kind:'ods-pixel-workspace-preview',relativeDirectory:'site',siteId,sha256:'c'.repeat(64),port:9437,url:`http://${siteId}.localhost:9437/${siteId}/`,files:1,bytes:1,entrySha256:'d'.repeat(64)};
 const preserved=[
  'Source: [fleet-check.txt](https://source.example.org/fleet-check.txt)',
  'Documentation: https://source.example.org/fleet-check.txt',
  '> Download: https://quoted.example.org/fleet-check.txt',
  '`Download: https://inline.example.org/fleet-check.txt`',
  '```text\nDownload: https://code.example.org/fleet-check.txt\n```',
  '    Download: https://indented.example.org/fleet-check.txt',
  '[Open website]('+preview.url+')',
  '[Download fleet-check.txt]('+preview.url+')',
 ].join('\n\n');
 const f=await fixture(t,{status:'passed',text:'The website snapshot is published.',deliveryMode:'append',preview,artifacts:[fleetArtifact]});
 f.setAnswer(`The Python tests failed; this task is incomplete.\n\n${preserved}\n\nDownload: ${inventedDownload}`);
 const result=await f.chat('mixed',[u('Build a website and deliver a document')]);
 assert.equal(result.status,200,JSON.stringify(result.value));
 const text=result.value.choices[0].message.content;
 assert.match(text,/tests failed; this task is incomplete/);assert.ok(text.includes(preserved),text);
 assert.match(text,/website snapshot is published/);assert.doesNotMatch(text,/portal\.openclaw\.ai/);
 assert.deepEqual(result.value.pixel_artifacts.artifacts,[fleetArtifact]);
});
test('same-name artifacts and conflicting link labels never select one download arbitrarily',async t=>{
 const second={...fleetArtifact,relativePath:'other/fleet-check.txt',siteId:'site-'+'e'.repeat(24),sha256:'e'.repeat(64),file:{...fleetArtifact.file,bytes:74,sha256:'f'.repeat(64)}};
 for(const [artifacts,answer] of [
  [[fleetArtifact,second],`Download: ${inventedDownload}`],
  [[fleetArtifact,artifact],`[Download report.md](${inventedDownload})`],
 ]) {
  const f=await fixture(t,{status:'none',artifacts});f.setAnswer(answer);
  const result=await f.chat('ambiguous',[u('Deliver both files')]);
  assert.equal(result.status,200);assert.match(result.value.choices[0].message.content,/filename alone is ambiguous/);
  assert.doesNotMatch(result.value.choices[0].message.content,/portal\.openclaw\.ai/);
  assert.deepEqual(result.value.pixel_artifacts.artifacts,artifacts);
 }
});
test('a Download prefix does not relabel later same-basename source links on that line',async t=>{
 const source='[source](https://docs.example/fleet-check.txt)';
 const f=await fixture(t,{status:'none',artifacts:[fleetArtifact]});
 f.setAnswer(`Download: [fleet-check.txt](${inventedDownload}). See ${source}.`);
 const result=await f.chat('same-line-source',[u('Deliver the file and cite its source')]);
 assert.equal(result.status,200);
 const text=result.value.choices[0].message.content;
 assert.doesNotMatch(text,/portal\.openclaw\.ai/);assert.ok(text.includes(source),text);
});
test('an undelivered filename label cannot borrow a different file receipt from its URL',async t=>{
 const f=await fixture(t,{status:'none',artifacts:[fleetArtifact]});
 f.setAnswer(`[Download other-report.txt](${inventedDownload})`);
 const result=await f.chat('contradictory-name',[u('Deliver my document')]);
 assert.equal(result.status,200);
 const text=result.value.choices[0].message.content;
 assert.match(text,/does not match the verified file receipt/);
 assert.doesNotMatch(text,/portal\.openclaw\.ai|for fleet-check\.txt|for other-report\.txt/);
 assert.deepEqual(result.value.pixel_artifacts.artifacts,[fleetArtifact]);
});
test('raw MEDIA and upstream-supplied artifact metadata cannot become delivered receipts',async t=>{
 const f=await fixture(t);f.setAnswer('MEDIA:project/report.md');f.setSpoof({schemaVersion:1,artifacts:[artifact]});
 const result=await f.chat('fake',[u('Report')]);assert.equal(result.status,200);assert.equal(result.value.pixel_artifacts,undefined);
 assert.equal(result.value.choices[0].message.content,'MEDIA:project/report.md');
});
test('empty text with verified document delivery describes the actual partial outcome',async t=>{
 const f=await fixture(t);f.setAnswer('NO_REPLY');f.setVerification({status:'none',artifacts:[artifact]});
 const result=await f.chat('empty-doc',[u('Report')]);assert.equal(result.status,200);
 assert.match(result.value.choices[0].message.content,/Verified file downloads are attached/);
 assert.doesNotMatch(result.value.choices[0].message.content,/without.*delivered result/);
 assert.deepEqual(result.value.pixel_artifacts.artifacts,[artifact]);
});
test('malformed, over-limit or pending artifact receipts fail closed without publishing metadata',async t=>{
 const invalid=[{status:'none',artifacts:[artifact,artifact]}, {status:'none',artifacts:[{...artifact,file:{...artifact.file,bytes:4194305}}]},
  {status:'none',artifacts:[{...artifact,relativePath:'../report.md'}]}, {status:'none',artifacts:[{...artifact,url:'http://evil'}]},
  {status:'pending',text:'Wait',artifacts:[artifact]}];
 for(const evidence of invalid){const f=await fixture(t);f.setVerification(evidence);const result=await f.chat('invalid',[u('Report')]);assert.equal(result.status,502);assert.equal(result.value.pixel_artifacts,undefined);}
});
test('incomplete attribution is a delivered outcome, not an interrupted turn',async t=>{
  const text='Research summary. Source check: attribution remains incomplete.';
  const f=await fixture(t,{status:'failed',text});
  const result=await f.chat('uncited',[u('Research official sources')]);
  assert.equal(result.status,200);
  assert.equal(result.value.choices[0].message.content,text);
  assert.equal(f.ledger.read(user).lastResult.verification.status,'failed');
  assert.notEqual(f.ledger.projection(user).status,'unknown');
  const next=await f.chat('next',[u('Research official sources'),a(text),u('Explain the limitation')]);
  assert.equal(next.status,200,'the next owner message needs no interrupted-turn resolution');
  assert.equal(f.calls.filter(c=>c.path==='/v1/chat/completions').length,2);
});
