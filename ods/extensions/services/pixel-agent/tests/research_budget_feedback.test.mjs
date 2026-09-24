import test from 'node:test';
import assert from 'node:assert/strict';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';

const tool={id:'openclaw:core:web_search',source:'openclaw',sourceName:'core',name:'web_search'};
const base={agentId:'pixel',runId:'research-budget',sessionId:'research-session',sessionKey:'agent:pixel:openai-user:ods-budget-test'};
const hint=message=>message?.content?.find(c=>c.text?.startsWith('ODS research budget'))?.text;
function begin(guard,transport,id,{empty=false,error=false,innerOnly=false}={}) {
 const context={...base,toolName:transport,toolCallId:id};
 const params=transport==='web_search'?{query:id}:{id:tool.id,args:{query:id}};
 assert.notEqual(guard.beforeToolCall({toolName:transport,params},context)?.block,true);
 if(transport==='tool_call')assert.notEqual(guard.beforeToolCall({toolName:'web_search',params:{query:id}},{...context,toolName:'web_search',toolCallId:'child-'+id})?.block,true);
 const details={provider:'test',results:empty?[]:[{title:'Source',url:'https://source.example/page',description:'Source excerpt',excerpts:['Source excerpt']}],externalContent:{untrusted:true}};
 const inner={content:[{type:'text',text:JSON.stringify(details)}],details,...(error?{isError:true}:{})};
 const envelope={tool,result:inner};
 guard.afterToolCall({toolName:transport,params,result:transport==='web_search'?inner:{content:[{type:'text',text:JSON.stringify(envelope)}],details:envelope}},context);
 const message={role:'toolResult',toolName:transport,toolCallId:id,...(error&&!innerOnly?{isError:true}:{})};
 return {context,message};
}
for(const transport of ['web_search','tool_call'])test(`${transport} reports real remaining allowances before denial, retaining exact evidence`,()=>{
 const guard=createToolLoopGuard({limits:{search:2,fetch:4,total:5}});
 guard.observeRun(base,'pixel',{prompt:'Research sources.'});
 for(let i=1;i<=2;i++){
  const {context,message}=begin(guard,transport,'search-'+i,{empty:i===2});
  const out=guard.toolResultPersist({message},context)?.message;
  assert.match(hint(out),new RegExp(`Remaining this response: ${2-i} search calls, ${5-i} page-reading calls, ${5-i} web calls total`));
  assert.match(hint(out),/read their actual URLs/);
  if(i===1)assert.match(hint(out),/Search again only for a specific unresolved evidence gap/);
  const original=transport==='web_search'?out.details:out.details.result.details;
  assert.deepEqual(original.results,i===2?[]:[{title:'Source',url:'https://source.example/page',description:'Source excerpt',excerpts:['Source excerpt']}]);
  assert.equal(guard.toolResultPersist({message},context),undefined);
 }
 // The feedback grants no calls; source reading remains independently allowed.
 assert.equal(guard.beforeToolCall({toolName:'web_search',params:{query:'denied'}},{...base,toolName:'web_search',toolCallId:'denied'})?.block,true);
 assert.notEqual(guard.beforeToolCall({toolName:'web_fetch',params:{url:'https://source.example/page'}},{...base,toolName:'web_fetch',toolCallId:'read'})?.block,true);
});
for(const mismatch of ['runId','sessionId','toolCallId'])test(`mismatched ${mismatch} cannot receive budget feedback`,()=>{
 const guard=createToolLoopGuard();guard.observeRun(base,'pixel',{prompt:'Research sources.'});
 const {context,message}=begin(guard,'web_search','bound');
 assert.equal(hint(guard.toolResultPersist({message},{...context,[mismatch]:'other'})?.message),undefined);
});
for(const transport of ['web_search','tool_call'])for(const innerOnly of [false,true])test(`${transport} failed search (inner-only=${innerOnly}) is not presented as usable leads`,()=>{
 const guard=createToolLoopGuard();guard.observeRun(base,'pixel',{prompt:'Research sources.'});
 const {context,message}=begin(guard,transport,'failed',{error:true,innerOnly});
 assert.equal(hint(guard.toolResultPersist({message},context)?.message),undefined);
});

// r037 Tower2: the last successful search said zero calls remain but still
// instructed "Search again", followed by four denied repeated searches.
for(const transport of ['web_search','tool_call'])test(`${transport} exhausted discovery does not invite another search`,()=>{
 const guard=createToolLoopGuard({limits:{search:1,fetch:4,total:5}});
 guard.observeRun(base,'pixel',{prompt:'Research sources.'});
 const {context,message}=begin(guard,transport,'last-search');
 const out=guard.toolResultPersist({message},context)?.message;
 assert.match(hint(out),/Remaining this response: 0 search calls, 4 page-reading calls, 4 web calls total/);
 assert.match(hint(out),/read their actual URLs with web_fetch or pixel_ods_web_extract/);
 assert.doesNotMatch(hint(out),/Search again/);
 assert.match(hint(out),/Do not (?:repeat|call) web_search/);
 assert.equal(guard.beforeToolCall({toolName:'web_search',params:{query:'denied'}},{...base,toolName:'web_search',toolCallId:'denied-again'})?.block,true);
 assert.notEqual(guard.beforeToolCall({toolName:'web_fetch',params:{url:'https://source.example/page'}},{...base,toolName:'web_fetch',toolCallId:'known-read'})?.block,true);
 // Legitimate undiscovered source reads stay allowed; advice is not a URL gate.
 assert.notEqual(guard.beforeToolCall({toolName:'web_fetch',params:{url:'https://other.example/new-source'}},{...base,toolName:'web_fetch',toolCallId:'new-read'})?.block,true);
});
