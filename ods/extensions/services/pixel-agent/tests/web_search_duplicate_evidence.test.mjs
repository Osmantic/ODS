import test from 'node:test';
import assert from 'node:assert/strict';
import { captureNativeWebSearchResult, projectNativeWebSearchResult, projectWebResult } from '../plugin/web-result-projection.mjs';
import { createToolLoopGuard } from '../plugin/tool-loop-guard.mjs';
const tool = {id:'openclaw:core:web_search',source:'openclaw',sourceName:'core',name:'web_search'};
const description = '<<<EXTERNAL_UNTRUSTED_CONTENT id="source-1">>>\nSource: Web Search\n餃子 café 😀\n<<<END_EXTERNAL_UNTRUSTED_CONTENT id="source-1">>>';
function payload() { return {provider:'parallel-free',searchQueries:['public test query'],count:2,externalContent:{untrusted:true},searchId:'search-fixture',sessionId:'provider-fixture',results:[
  {title:'Wrapped title',url:'https://example.com/source',description,siteName:'example.com',published:'2026-01-02',excerpts:[description,'unique passage',description+'\n']},
  {title:'Second source',url:'https://other.example/page',description:'another description',excerpts:[description,'other unique evidence']},
]}; }
function result(p=payload()) { return {content:[{type:'text',text:JSON.stringify(p,null,2),annotations:{audience:['assistant']}}],details:p}; }
const message = {role:'toolResult',toolName:'web_search',toolCallId:'call-1',content:[{type:'text',text:'{framework truncation'}],details:{persistedDetailsTruncated:true}};
function expected(p) {const out=structuredClone(p);out.results[0].excerpts=out.results[0].excerpts.filter(x=>x!==out.results[0].description);return out;}

test('native projection removes only exact same-result duplicate strings and keeps original structured receipt',()=>{
 const input=result();const before=structuredClone(input);const projected=projectNativeWebSearchResult(message,input);
 assert.deepEqual(JSON.parse(projected.content[0].text),expected(input.details));assert.deepEqual(projected.details,before.details);
 assert.deepEqual(projected.content[0].annotations,input.content[0].annotations);assert.deepEqual(input,before);
 assert.equal(projected.toolCallId,message.toolCallId);assert.ok(projected.content[0].text.includes('EXTERNAL_UNTRUSTED_CONTENT'));
 assert.equal(JSON.parse(projected.content[0].text).results[1].excerpts[0],description,'never deduplicate across sources');
});
test('deferred projection shares exact deduplication and retains full original envelope',()=>{
 const inner=result();const envelope={tool,result:inner};const projected=projectWebResult({...message,toolName:'tool_call'},envelope);
 assert.deepEqual(JSON.parse(projected.content[1].text),expected(inner.details));assert.deepEqual(projected.details,envelope);
 assert.equal(JSON.parse(projected.content[0].text).result.details,undefined);assert.deepEqual(envelope.result,inner);
});
test('only description-equal excerpt removal can delete the excerpts key',()=>{
 const p=payload();p.results[0].excerpts=[description];const projected=projectNativeWebSearchResult(message,result(p));
 assert.equal(Object.hasOwn(JSON.parse(projected.content[0].text).results[0],'excerpts'),false);
 assert.equal(projected.details.results[0].excerpts[0],description);
});
test('no duplicates preserve exact native evidence and add separate source guidance',()=>{
 const p=payload();p.results[0].excerpts=['unique'];const input=result(p);const before=structuredClone(input);
 const projected=projectNativeWebSearchResult(message,input);
 assert.deepEqual(projected.content[0],input.content[0]);assert.deepEqual(projected.details,p);
 assert.match(projected.content[1].text,/not source evidence/);assert.match(projected.content[1].text,/publisher identity/);
 assert.deepEqual(captureNativeWebSearchResult(input),before);assert.deepEqual(input,before);
});
test('fetch projection does not reinterpret similarly shaped page evidence',()=>{
 const fetchTool={...tool,id:'openclaw:core:web_fetch',name:'web_fetch'};const inner=result();
 assert.equal(projectWebResult({...message,toolName:'tool_call'},{tool:fetchTool,result:inner}).content[1].text,inner.content[0].text);
 assert.equal(projectNativeWebSearchResult({...message,toolName:'web_fetch'},inner),undefined);
});
for (const [name,change] of [
 ['truncated JSON',r=>{r.content[0].text=r.content[0].text.slice(0,20);}],
 ['different structured receipt',r=>{r.details.count=99;}],
 ['image block',r=>r.content.push({type:'image',data:'opaque'})],
 ['split text blocks',r=>r.content.push({type:'text',text:'other evidence'})],
 ['missing provider',r=>{delete r.details.provider;r.content[0].text=JSON.stringify(r.details);}],
 ['malformed excerpts',r=>{r.details.results[0].excerpts=[null];r.content[0].text=JSON.stringify(r.details);}],
 ['oversized text',r=>{r.content[0].text=' '.repeat(256*1024)+r.content[0].text;}],
 ['too many sources',r=>{r.details.results=Array(41).fill(r.details.results[0]);r.content[0].text=JSON.stringify(r.details);}],
]) test(`unsupported ${name} is not captured or rewritten`,()=>{
 const input=result();change(input);const before=structuredClone(input);
 assert.equal(captureNativeWebSearchResult(input),undefined);assert.equal(projectNativeWebSearchResult(message,input),undefined);assert.deepEqual(input,before);
});
function captured(transport='web_search',changeEvent=()=>{},changeContext=()=>{}) {
 const guard=createToolLoopGuard();const context={agentId:'pixel',sessionId:'session-1',sessionKey:'agent:pixel:openai-user:ods-public-fixture',runId:'run-1',toolName:transport,toolCallId:'call-1'};
 const params=transport==='tool_call'?{id:tool.id,args:{query:'public test query'}}:{query:'public test query'};
 guard.observeRun(context,'pixel',{prompt:'Research public sources and summarize supported facts.'});
 assert.notEqual(guard.beforeToolCall({toolName:transport,params},context)?.block,true);
 const original=result();const envelope={tool,result:original};const event={toolName:transport,toolCallId:'call-1',runId:'run-1',params,result:transport==='tool_call'?{content:[{type:'text',text:JSON.stringify(envelope)}],details:envelope}:original};
 const afterContext={...context};changeEvent(event);changeContext(afterContext);guard.afterToolCall(event,afterContext);
 return {guard,context,original,transport};
}
function persist(run,eventChange=()=>{},contextChange=()=>{}) {
 const event={message:{...message,toolName:run.transport}};const context={...run.context};eventChange(event);contextChange(context);return run.guard.toolResultPersist(event,context);
}
for (const transport of ['web_search','tool_call']) {
 test(`${transport} exact call capture survives later persistence truncation`,()=>{
  const run=captured(transport);const reply=persist(run);assert.ok(reply);
  const content=reply.message.content[transport==='tool_call'?1:0];assert.deepEqual(JSON.parse(content.text),expected(run.original.details));
  assert.equal(persist(run),undefined,'capture consumed exactly once');
 });
 for (const [name,eventChange,contextChange] of [
  ['changed args',e=>{if(transport==='tool_call')e.params.args={query:'other'};else e.params={query:'other'};},()=>{}],
  ['wrong event call',e=>{e.toolCallId='other-call';},()=>{}],
  ['wrong event run',e=>{e.runId='other-run';},()=>{}],
  ['wrong event tool',e=>{e.toolName='exec';},()=>{}],
  ['wrong session',()=>{},c=>{c.sessionId='other-session';}],
 ]) test(`${transport} refuses after-tool capture for ${name}`,()=>assert.equal(persist(captured(transport,eventChange,contextChange)),undefined));
 for (const [name,eventChange,contextChange] of [
  ['wrong message call',e=>{e.message.toolCallId='other-call';},()=>{}],
  ['wrong event call',e=>{e.toolCallId='other-call';},()=>{}],
  ['wrong event run',e=>{e.runId='other-run';},()=>{}],
  ['wrong context run',()=>{},c=>{c.runId='other-run';}],
  ['wrong session',()=>{},c=>{c.sessionId='other-session';}],
 ]) test(`${transport} refuses persistence for ${name}`,()=>assert.equal(persist(captured(transport),eventChange,contextChange),undefined));
}
test('native capture snapshots the pre-truncation original without retaining later mutations',()=>{
 const run=captured();const original=structuredClone(run.original);run.original.content[0].text='mutated';run.original.details.results[0].description='changed';
 const reply=persist(run);assert.deepEqual(reply.message.details,original.details);assert.deepEqual(JSON.parse(reply.message.content[0].text),expected(original.details));
});
test('native projection preserves failure state',()=>{
 const input=result();input.isError=true;assert.equal(projectNativeWebSearchResult(message,input).isError,true);
 assert.equal(projectNativeWebSearchResult({...message,isError:true},result()).isError,true);
});
// This is the pinned runtime sanitizer's UTF-16 boundary, not a new allowance.
function nativeSanitize(text) {
 if(text.length<=8000)return text;
 let end=8000;
 if(text.charCodeAt(end-1)>=0xD800 && text.charCodeAt(end-1)<=0xDBFF && text.charCodeAt(end)>=0xDC00 && text.charCodeAt(end)<=0xDFFF)end--;
 return text.slice(0,end)+'\n…(truncated)…';
}
function largeResult() {
 const p=payload();p.results[0].description=description+'x'.repeat(9000);p.results[0].excerpts=[p.results[0].description,'unique late evidence'];
 p.warning='Untrusted search evidence; no source has been independently verified.';
 return result(p);
}
test('exact native sanitizer prefix can recover complete leads within the same cap',()=>{
 const input=largeResult();const full=input.content[0].text;input.content[0].text=nativeSanitize(full);const original=structuredClone(input);
 const projected=projectNativeWebSearchResult(message,input);
 const evidence=JSON.parse(projected.content[0].text),wanted=expected(input.details);delete wanted.results[0].description;
 assert.deepEqual(evidence,wanted);assert.ok(projected.content[0].text.length<=8000);
 assert.match(projected.content[2].text,/Some descriptions or excerpts were omitted/);
 assert.deepEqual(projected.details,original.details);assert.deepEqual(input,original);
 assert.equal(projected.details.warning,input.details.warning);assert.equal(projected.details.results[0].excerpts[1],'unique late evidence');
});
test('deduplication of a capped native result can expose unique evidence within the unchanged cap',()=>{
 const p=payload();p.results[0].description='x'.repeat(4500);p.results[0].excerpts=[p.results[0].description,'unique late evidence'];
 const input=result(p);assert.ok(input.content[0].text.length>8000);input.content[0].text=nativeSanitize(input.content[0].text);
 const projected=projectNativeWebSearchResult(message,input);assert.ok(projected.content[0].text.length<8000);
 assert.deepEqual(JSON.parse(projected.content[0].text),expected(p));assert.ok(projected.content[0].text.includes('unique late evidence'));
});
test('native complete hook input is also capped after projection',()=>{
 const input=largeResult();const projected=projectNativeWebSearchResult(message,input);
 assert.ok(projected.content[0].text.length<=8000);
 assert.deepEqual(JSON.parse(projected.content[0].text).results.map(r=>r.url),input.details.results.map(r=>r.url));
});
test('native projection preserves surrogate pairs at the exact sanitizer boundary',()=>{
 const p=payload();p.results[0].description='';p.results[0].excerpts=[''];
 const offset=JSON.stringify(p,null,2).indexOf('"description": "')+'"description": "'.length;
 p.results[0].description='x'.repeat(7999-offset)+'😀tail';p.results[0].excerpts=[p.results[0].description,'unique'];
 const input=result(p);assert.equal(input.content[0].text.charCodeAt(7999),0xD83D);input.content[0].text=nativeSanitize(input.content[0].text);
 assert.equal(input.content[0].text.slice(0,-'\n…(truncated)…'.length).length,7999);
 const projected=projectNativeWebSearchResult(message,input);
 assert.ok(projected.content[0].text.length<=8000);assert.doesNotThrow(()=>JSON.parse(projected.content[0].text));
 assert.equal(projected.details.results[0].description,p.results[0].description);
});
for(const [name,change] of [
 ['wrong prefix',r=>{r.content[0].text='!'+r.content[0].text.slice(1);}],
 ['wrong suffix',r=>{r.content[0].text=r.content[0].text.replace('…(truncated)…','...(truncated)...');}],
 ['arbitrary early prefix',r=>{r.content[0].text=r.content[0].text.slice(0,3000)+'\n…(truncated)…';}],
 ['redacted mismatch',r=>{r.details.provider='redacted-provider';}],
 ['lossy structured value',r=>{r.details.count=NaN;r.content[0].text=nativeSanitize(JSON.stringify(r.details,null,2));}],
 ['non-string serialization',r=>{r.details.toJSON=()=>undefined;}],
 ['oversized structured receipt',r=>{r.details.results[0].description='x'.repeat(300000);r.details.results[0].excerpts=[r.details.results[0].description];r.content[0].text=nativeSanitize(JSON.stringify(r.details,null,2));}],
])test(`native capped ${name} is not reconstructed`,()=>{
 const input=largeResult();input.content[0].text=nativeSanitize(input.content[0].text);change(input);
 assert.equal(captureNativeWebSearchResult(input),undefined);assert.equal(projectNativeWebSearchResult(message,input),undefined);
});
for(const transport of ['web_search','tool_call'])test(`${transport} pending result from an earlier observed run is not projected`,()=>{
 const run=captured(transport);run.guard.observeRun({...run.context,runId:'run-2'},'pixel',{prompt:'A different owner request.'});
 assert.equal(persist(run),undefined);
});
