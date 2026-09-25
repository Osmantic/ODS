import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const manifest=JSON.parse(readFileSync(new URL('../host/openclaw-compaction-budget.json',import.meta.url)));
const source=manifest.replacements.find(([before])=>before==='function normalizeMessagesForLlmBoundary(messages, options) {')[1];
const helper=source.slice(0,source.lastIndexOf('function normalizeMessagesForLlmBoundary'));
const create=vm.runInNewContext(helper+'\ncreatePersistedToolResultModelProjection',{structuredClone,Map,JSON,Number});
const receipt=(extra={})=>({role:'toolResult',toolName:'exec',toolCallId:'one',timestamp:123,isError:true,content:[{type:'text',text:'failure\nuse the real output'}],details:{status:'completed',exitCode:1},...extra});

test('projects final persisted feedback without changing stored evidence or rerunning hooks',()=>{
 const state=create(), persisted=receipt(), original=receipt({content:[{type:'text',text:'failure'}]});
 state.capture(persisted);
 const out=state.apply([original]);
 assert.deepEqual(out[0],persisted);assert.notEqual(out[0],persisted);assert.equal(original.content[0].text,'failure');
 assert.deepEqual(state.apply([original]),out,'same receipt, not another hook execution');
 out[0].content[0].text='mutated';persisted.details.exitCode=0;
 assert.equal(state.apply([original])[0].details.exitCode,1);
 assert.equal(state.apply([original])[0].content[0].text,'failure\nuse the real output');
});
test('current attempt only: another run or session cannot inherit the projection',()=>{
 const a=create(),b=create(), rows=[receipt({content:[]})];a.capture(receipt());
 assert.equal(b.apply(rows),rows);assert.notEqual(a.apply(rows),rows);
});
for(const [name,change] of [['call',{toolCallId:'other'}],['tool',{toolName:'read'}],['timestamp',{timestamp:124}],['role',{role:'assistant'}],['missing timestamp',{timestamp:undefined}]])test(`projection rejects different ${name}`,()=>{
 const state=create();state.capture(receipt());const rows=[receipt(change)];assert.equal(state.apply(rows),rows);
});
test('ambiguous repeated persisted identity never supplies feedback',()=>{
 const state=create();state.capture(receipt());state.capture(receipt());const rows=[receipt({content:[]})];assert.equal(state.apply(rows),rows);
});
test('duplicate live identities are left unchanged and unmatched rows are not manufactured',()=>{
 const state=create();state.capture(receipt());const rows=[receipt({content:[]}),receipt({content:[]})];assert.equal(state.apply(rows),rows);assert.equal(state.apply([]).length,0);
});
test('bounded capture refuses new entries at capacity',()=>{
 const state=create();for(let i=0;i<512;i++)state.capture(receipt({toolCallId:String(i)}));state.capture(receipt({toolCallId:'overflow'}));const rows=[receipt({toolCallId:'overflow'})];assert.equal(state.apply(rows),rows);
});
test('projection preserves multimodal and failure metadata together',()=>{
 const state=create(), full=receipt({content:[{type:'image',mimeType:'image/png',data:'fixture'},{type:'text',text:'inspection failed'}]});state.capture(full);
 assert.deepEqual(state.apply([receipt()])[0],full);
});
test('runtime wiring projects before existing provider truncation and preserves all previous repairs',()=>{
 const wire=manifest.replacements.map(x=>x[1]).join('\n');
 assert.match(wire,/truncateOversizedToolResultsInMessages\(persistedToolResultModelProjection.apply\(messages\), contextTokenBudget,/);
 assert.match(wire,/onMessagePersisted: \(message\) => \{\n\s+persistedToolResultModelProjection.capture\(message\);\n\s+sessionLockController.refreshAfterOwnedSessionWrite\(\);/);
 const prior=manifest.previousReplacements['65fb7b9ed2fadfa3c0019c88484e77e355c6db4650feda7f39c43db99aaa2e61'];
 assert.deepEqual(manifest.replacements.slice(0,prior.length),prior);
});
