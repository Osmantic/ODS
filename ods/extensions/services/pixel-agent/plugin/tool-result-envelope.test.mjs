import test from 'node:test';
import assert from 'node:assert/strict';
import {compactToolResultEnvelope} from './tool-result-envelope.mjs';

test('removes repeated discovery metadata without losing the execution receipt', () => {
  const result = {isError:true, content:[{type:'text', text:'Actual failure details'}],
    details:{operationId:'saved-id', state:'uncertain'}};
  const envelope = {tool:{id:'openclaw:pixel-ods:example', name:'example',
    label:'Example', description:'Long discovery contract. '.repeat(300), source:'openclaw',sourceName:'pixel-ods'},result};
  const message = {toolName:'tool_call',toolCallId:'call-1',isError:true,
    details:envelope,content:[{type:'text',text:JSON.stringify(envelope)},{type:'image',data:'unchanged'}]};
  const projected = compactToolResultEnvelope(message);
  assert.deepEqual(JSON.parse(projected.content[0].text), {
    tool:{id:'openclaw:pixel-ods:example',name:'example'}, result});
  assert.equal(projected.details,message.details);
  assert.equal(projected.toolCallId,'call-1');
  assert.equal(projected.isError,true);
  assert.equal(projected.content[1],message.content[1]);
  assert.ok(projected.content[0].text.length < message.content[0].text.length / 4);
  assert.equal(compactToolResultEnvelope(projected),projected);
});

test('leaves ordinary evidence, search results and malformed envelopes alone', () => {
  for (const message of [undefined, {toolName:'read',content:[{type:'text',text:'evidence'}]},
    ...['plain evidence','[]','{"result": {}}','{"tool":{"id":"other","name":"x"},"result":{}}']
      .map(text => ({toolName:'tool_call',content:[{type:'text',text}]}))]) {
    assert.equal(compactToolResultEnvelope(message),message);
  }
});
