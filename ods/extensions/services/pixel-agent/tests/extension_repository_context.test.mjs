import test from 'node:test';
import assert from 'node:assert/strict';
import {createExtensionRepositoryContext} from '../plugin/extension-repository-context.mjs';

test('grounds the exact requested project once, without installation or model calls', async () => {
  const calls=[];
  const context=createExtensionRepositoryContext({tool:{execute:async (...args)=>{
    calls.push(args);return {content:[{type:'text',text:'Untrusted README evidence'}]};
  }}});
  const event={prompt:'/extensions https://github.com/NandhaKishorM/laya analise antes de instalar'};
  const [a,b]=await Promise.all([context(event),context(event)]);
  assert.equal(a,b);assert.equal(calls.length,1);
  assert.equal(calls[0][1].url,'https://raw.githubusercontent.com/NandhaKishorM/laya/HEAD/README.md');
  assert.ok(calls[0][2] instanceof AbortSignal);
  assert.match(a,/not installation authorization/);
  assert.match(a,/Untrusted README evidence/);
});

test('does not fetch historical, non-command, catalog, private or ambiguous requests', async () => {
  const context=createExtensionRepositoryContext({tool:{execute:()=>{throw new Error('unexpected fetch')}}});
  for(const prompt of ['hello','/extensions @laya','/extensions http://127.0.0.1/',
    'Quoted example: /extensions https://github.com/a/b',
    '/extensions https://github.com/a/b\n[Current message - respond to this]\nUser: hello',
    '[Current message - respond to this]\nUser: /extensions https://github.com/a/b\n[Current message - respond to this]\nUser: hi']) {
    assert.equal(await context({prompt}),'');
  }
});

test('reads current wrapped requests and structured user content',async()=>{
  let count=0;
  const context=createExtensionRepositoryContext({tool:{execute:async()=>{count++;return {content:[{type:'text',text:'README'}]};}}});
  assert.match(await context({prompt:'History\n[Current message - respond to this]\nUser: /extension https://github.com/a/b'}),/README/);
  assert.match(await context({messages:[{role:'user',content:[{type:'text',text:'/extensions https://github.com/c/d'}]}]}),/README/);
  assert.match(await context({prompt:'/extensions install https://github.com/e/f'}),/README/);
  assert.equal(count,3);
});

test('failed reads remain explicit and retryable; cached evidence expires',async()=>{
  let count=0,clock=0;
  const context=createExtensionRepositoryContext({now:()=>clock,tool:{execute:async()=>{
    count++;return count===1 ? {isError:true} : {content:[{type:'text',text:'README'}]};
  }}});
  const event={prompt:'/extensions https://github.com/a/b'};
  assert.match(await context(event),/documentation read failed/);
  assert.match(await context(event),/README/);
  await context(event);assert.equal(count,2);
  clock=60001;await context(event);assert.equal(count,3);
});


test('GitHub installation guidance survives failed evidence reads and goal wrappers', async () => {
  for (const prefix of ['', '/goal ']) {
    for (const result of [{isError: true}, {content: [{type: 'text', text: 'README'}]}]) {
      const context = createExtensionRepositoryContext({tool: {execute: async () => result}});
      const value = await context({prompt: `${prefix}/extensions https://github.com/a/b install`});
      assert.match(value, /pixel_ods_extension_proposal/);
      assert.match(value, /pixel_ods_web_extract/);
      assert.match(value, /pixel_ods_extension_request_advance/);
      assert.match(value, /pending operation must be observed/);
      assert.match(value, /A draft alone never proves installation/);
      assert.match(value, /Honor research-only requests/);
      assert.match(value, /sandbox does not register an ODS extension/);
    }
  }
});

test('large README is a bounded explicitly truncated evidence block', async () => {
  const context = createExtensionRepositoryContext({tool: {execute: async () => ({
    content: [{type: 'text', text: 'x'.repeat(100000) + 'OMITTED_END'}],
  })}});
  const value = await context({prompt: '/extensions https://github.com/a/b install'});
  assert.match(value, /"truncated":true/);
  assert.doesNotMatch(value, /OMITTED_END/);
  assert.ok(value.length < 13000);
});
