import test from 'node:test';
import assert from 'node:assert/strict';
import {captureNativeWebSearchResult,projectNativeWebSearchResult,projectWebResult,EMPTY_SEARCH_RECOVERY_GUIDANCE,SEARCH_SOURCE_EVIDENCE_GUIDANCE} from '../plugin/web-result-projection.mjs';
import {createToolLoopGuard,DEFAULT_WEB_TOOL_LIMITS} from '../plugin/tool-loop-guard.mjs';
const tool={id:'openclaw:core:web_search',source:'openclaw',sourceName:'core',name:'web_search'};
const message={role:'toolResult',toolName:'web_search',toolCallId:'call'};
function result(results=[]){const details={provider:'search-provider',results,query:'exact owner query',externalContent:{untrusted:true}};return {content:[{type:'text',text:JSON.stringify(details)}],details};}
for(const transport of ['web_search','tool_call'])test(`${transport} empty result keeps exact receipt and separate recovery guidance`,()=>{
 const input=result(),before=structuredClone(input);
 const projected=transport==='web_search'?projectNativeWebSearchResult(message,input):projectWebResult({...message,toolName:transport},{tool,result:input});
 const offset=transport==='web_search'?0:1;
 assert.deepEqual(projected.content[offset],input.content[0]);
 assert.equal(projected.content[offset+1].text,EMPTY_SEARCH_RECOVERY_GUIDANCE);
 assert.deepEqual(transport==='web_search'?projected.details:projected.details.result.details,input.details);
 assert.deepEqual(input,before);assert.deepEqual(captureNativeWebSearchResult(input),before);
});
test('nonempty ambiguous variants remain evidence, never normalized or treated as verified',()=>{
 const input=result([{title:'Related model XT',url:'https://reseller.example/xt',description:'Claimed official'}]);
 const projected=projectNativeWebSearchResult(message,input);
 assert.equal(projected.content[0].text,input.content[0].text);
 assert.equal(projected.content[1].text,SEARCH_SOURCE_EVIDENCE_GUIDANCE);
 assert.match(projected.content[1].text,/resellers and aggregators are independent/);
});
for(const change of [r=>{r.isError=true;},r=>{r.details.results=null;},r=>{r.content[0].text='{"results":[]';},r=>{r.details.results=[{url:'different'}];}])test('error or mismatched empty evidence does not receive recovery guidance',()=>{
 const input=result();change(input);assert.equal(captureNativeWebSearchResult(input),undefined);assert.equal(projectNativeWebSearchResult(message,input),undefined);
});
for(const transport of ['web_search','tool_call'])test(`${transport} empty recovery is exact-call bound and does not extend search allowance`,()=>{
 const guard=createToolLoopGuard();const base={agentId:'pixel',runId:'empty-run',sessionId:'empty-session',sessionKey:'agent:pixel:openai-user:ods-public-test'};
 guard.observeRun(base,'pixel',{prompt:'Research requested sources.'});
 for(let i=0;i<DEFAULT_WEB_TOOL_LIMITS.search;i++){
  const query='entity '+i,params=transport==='web_search'?{query}:{id:tool.id,args:{query}};
  const context={...base,toolName:transport,toolCallId:'empty-'+i};
  assert.notEqual(guard.beforeToolCall({toolName:transport,params},context)?.block,true);
  if(transport==='tool_call')assert.notEqual(guard.beforeToolCall({toolName:'web_search',params:{query}},{...context,toolName:'web_search',toolCallId:'child-'+i})?.block,true);
  const inner=result(),envelope={tool,result:inner};
  guard.afterToolCall({toolName:transport,params,result:transport==='web_search'?inner:{content:[{type:'text',text:JSON.stringify(envelope)}],details:envelope}},context);
  const persisted=guard.toolResultPersist({message:{role:'toolResult',toolName:transport,toolCallId:context.toolCallId}},context);
  assert.ok(persisted.message.content.some(c=>c.text===EMPTY_SEARCH_RECOVERY_GUIDANCE));
  assert.equal(params.query??params.args.query,query);
  assert.equal(guard.toolResultPersist({message:{role:'toolResult',toolName:transport,toolCallId:context.toolCallId}},context),undefined);
 }
 const params=transport==='web_search'?{query:'another'}:{id:tool.id,args:{query:'another'}};
 guard.beforeToolCall({toolName:transport,params},{...base,toolName:transport,toolCallId:'blocked-outer'});
 assert.equal(guard.beforeToolCall({toolName:'web_search',params:{query:'another'}},{...base,toolName:'web_search',toolCallId:'blocked'}).block,true);
 assert.notEqual(guard.beforeToolCall({toolName:'web_fetch',params:{url:'https://source.example/page'}},{...base,toolName:'web_fetch',toolCallId:'read'} )?.block,true);
});
