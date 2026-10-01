import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createChatImageReadTool} from '../plugin/chat-image-read.mjs';

const user = `ods-${'a'.repeat(64)}`, sessionId = 'owner-session';
const context = {agentId:'pixel', sessionKey:`agent:pixel:openai-user:${user}`, sessionId};
const bytes = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aR9kAAAAASUVORK5CYII=','base64');
const args = {id:`img-${'1'.repeat(32)}`, sha256:createHash('sha256').update(bytes).digest('hex')};
const envelope = () => ({schemaVersion:1,image:{...args,mimeType:'image/png',bytes:bytes.length,data:bytes.toString('base64')}});
const supported = {imageInput:'supported',routeFingerprint:'d'.repeat(64),unknownConsent:false};
function factory(changes={}, ctx=context) {
  const calls=[];
  const deps = {
    getSessionEntry:async scope => {calls.push(['session',scope]); return {sessionId};},
    readConfig:() => ({session:{store:'/private/session-store'}}),
    resolveStorePath:value => value,
    imagePolicyForContext:async () => supported,
    readImage:async (...request) => {calls.push(['read',...request]); return envelope();},
    ...changes,
  };
  return {tool:createChatImageReadTool(ctx,deps), calls};
}

test('verified native image content is returned after transcript pruning, never only its reference', async () => {
  const {tool,calls} = factory();
  const result = await tool.execute('call',args);
  assert.equal(result.isError,undefined);
  assert.equal(result.content[1].type,'image');
  assert.deepEqual(Buffer.from(result.content[1].data,'base64'),bytes);
  assert.deepEqual(calls.find(call=>call[0]==='read').slice(1,3),[user,args]);
  assert.equal(calls.filter(call=>call[0]==='session').length,2);
  assert.equal(result.details.sha256,args.sha256);
});

test('explicit unsupported and unconsented unknown routes do not fetch bytes', async () => {
  for (const [policy,code] of [[{...supported,imageInput:'unsupported'},'image-input-unsupported'],
    [{...supported,imageInput:'unknown'},'image-input-consent-required'], [undefined,'image-input-consent-required']]) {
    const {tool,calls}=factory({imagePolicyForContext:async()=>policy});
    assert.equal((await tool.execute('call',args)).details.errorCode,code);
    assert.equal(calls.some(call=>call[0]==='read'),false);
  }
  const {tool}=factory({imagePolicyForContext:async()=>({...supported,imageInput:'unknown',unknownConsent:true})});
  assert.equal((await tool.execute('call',args)).content[1].type,'image');
});

test('missing/changed SDK session and child contexts cannot borrow owner images', async () => {
  for (const ctx of [{...context,agentId:'other'}, {...context,sessionKey:'agent:pixel:subagent:abc'},
    {...context,sessionId:undefined}, {...context,sessionKey:context.sessionKey+'/../other'}]) {
    const {tool,calls}=factory({},ctx);
    assert.equal((await tool.execute('call',args)).details.errorCode,'image-session-unavailable');
    assert.equal(calls.some(call=>call[0]==='read'),false);
  }
  for (const entry of [null,{sessionId:'another-session'}]) {
    const {tool}=factory({getSessionEntry:async()=>entry});
    assert.equal((await tool.execute('call',args)).details.errorCode,'image-session-unavailable');
  }
});

test('model arguments cannot provide user, path, URL or consent', async () => {
  const {tool,calls}=factory();
  for (const request of [{...args,user}, {...args,url:'http://host/secret'}, {...args,path:'/etc/passwd'},
    {...args,unknownConsent:true}, {...args,id:'../../secret'}]) {
    assert.equal((await tool.execute('call',request)).details.errorCode,'invalid-image-reference');
  }
  assert.deepEqual(calls,[]);
});

test('exact tool and context grammars reject newline suffixes and wrong types', async () => {
  for (const suffix of ['\n','\r\n']) {
    const {tool,calls}=factory();
    for (const request of [{...args,id:args.id+suffix},{...args,sha256:args.sha256+suffix}]) {
      assert.equal((await tool.execute('call',request)).details.errorCode,'invalid-image-reference');
    }
    assert.deepEqual(calls,[]);
    assert.equal((await factory({}, {...context,sessionKey:context.sessionKey+suffix}).tool.execute('call',args)).details.errorCode,'image-session-unavailable');
    assert.equal((await factory({imagePolicyForContext:async()=>({...supported,routeFingerprint:supported.routeFingerprint+suffix})}).tool.execute('call',args)).details.errorCode,'image-input-consent-required');
  }
  for (const value of [null,0,[],{}]) {
    assert.equal((await factory().tool.execute('call',{...args,id:value})).details.errorCode,'invalid-image-reference');
    assert.equal((await factory().tool.execute('call',{...args,sha256:value})).details.errorCode,'invalid-image-reference');
  }
});

test('route change, session replacement or cancellation during read discards image', async () => {
  let policyCalls=0;
  const {tool}=factory({imagePolicyForContext:async()=>({...supported,routeFingerprint:(++policyCalls===1?'d':'e').repeat(64)})});
  assert.equal((await tool.execute('call',args)).details.errorCode,'image-read-unavailable');
  let sessionCalls=0;
  const changed=factory({getSessionEntry:async()=>({sessionId:++sessionCalls===1?sessionId:'new-session'})});
  assert.equal((await changed.tool.execute('call',args)).details.errorCode,'image-session-unavailable');
  const abort=new AbortController();
  const canceled=factory({readImage:async()=>{abort.abort();return envelope();}});
  assert.equal((await canceled.tool.execute('call',args,abort.signal)).isError,true);
});

test('invalid/private transport data yields bounded diagnostics without image or sensitive values', async () => {
  for (const value of [{...envelope(),url:'http://secret'}, {schemaVersion:1,image:{...envelope().image,data:'AAAA'}},
    {schemaVersion:1,image:{...envelope().image,mimeType:'image/svg+xml'}},
    {schemaVersion:1,image:{...envelope().image,sha256:'f'.repeat(64)}}]) {
    const {tool}=factory({readImage:async()=>value});
    const result=await tool.execute('call',args);
    assert.equal(result.isError,true);
    assert.equal(result.content.some(part=>part.type==='image'),false);
    assert.ok(!JSON.stringify(result).includes('secret'));
  }
  const {tool}=factory({readImage:async()=>{throw new Error('secret-token /private/host/file');}});
  assert.equal((await tool.execute('call',args)).details.errorCode,'image-read-unavailable');
});
