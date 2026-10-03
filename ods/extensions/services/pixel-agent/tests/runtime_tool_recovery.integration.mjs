import assert from 'node:assert/strict';
import test from 'node:test';
if (!process.env.OPENCLAW_LOOP_MODULE) throw new Error('Set OPENCLAW_LOOP_MODULE to the real reviewed runtime module');
const { n: record, r: outcome, t: detect } = await import(process.env.OPENCLAW_LOOP_MODULE);
const config={enabled:true,unknownToolThreshold:2,warningThreshold:10,criticalThreshold:20,globalCircuitBreakerThreshold:30};
const scope={runId:'run-a'};
function miss(state,id,callId,options={}) {
 const tool=options.tool || 'tool_call';
 const params=options.params || {id,args:{attempt:callId}};
 record(state,tool,params,callId,config,options.scope || scope);
 return outcome(state,{toolName:tool,toolParams:params,toolCallId:callId,config,runId:(options.scope||scope).runId,error:options.error || `Unknown tool id: ${id}. Use tool_search to find a tool.`});
}
test('actual discovery error captures the missing target, not the word id',()=>{
 const s={}; const r=miss(s,'tool_describe','one');
 assert.equal(r.unknownToolName,'tool_describe');
});
test('two different discovery misses do not fuse a valid exec or read',()=>{
 const s={}; miss(s,'tool_describe','one');miss(s,'tool_search','two');
 assert.equal(detect(s,'tool_call',{id:'exec',args:{command:'printf recovered'}},config,scope).stuck,false);
 assert.equal(detect(s,'tool_call',{id:'read',args:{path:'safe.txt'}},config,scope).stuck,false);
});
test('same unknown target repeats are still stopped even with changed arguments',()=>{
 const s={};miss(s,'missing','one');miss(s,'missing','two');
 const r=detect(s,'tool_call',{id:'missing',args:{attempt:3}},config,scope);
 assert.equal(r.stuck,true);assert.equal(r.detector,'unknown_tool_repeat');assert.match(r.message,/unavailable tool missing 2 times/);
 assert.equal(detect(s,'tool_call',{id:'exec',args:{}},config,scope).stuck,false);
});
test('namespaced and dotted IDs preserve identity without sentence punctuation',()=>{
 for(const id of ['openclaw:core:missing','plugin.tool-name','plugin:source:tool.name']) {
  const s={}; assert.equal(miss(s,id,'one').unknownToolName,id);miss(s,id,'two');
  assert.equal(detect(s,'tool_call',{id,args:{}},config,scope).detector,'unknown_tool_repeat');
  assert.equal(detect(s,'tool_call',{id:'openclaw:core:exec',args:{}},config,scope).stuck,false);
 }
});
test('quoted legacy errors and direct tools remain protected',()=>{
 for(const error of ['Unknown tool: "missing"',"Tool 'missing' is not available",'Unknown tool missing']) {
  const s={};miss(s,'missing','one',{tool:'missing',params:{attempt:1},error});miss(s,'missing','two',{tool:'missing',params:{attempt:2},error});
  assert.equal(detect(s,'missing',{attempt:3},config,scope).detector,'unknown_tool_repeat');
 }
});
test('no shared unknown streak across run scope or malformed current IDs',()=>{
 const s={};miss(s,'missing','one');miss(s,'missing','two');
 assert.equal(detect(s,'tool_call',{id:'missing'},config,{runId:'run-b'}).stuck,false);
 for(const params of [{},{id:null},{id:42}])assert.equal(detect(s,'tool_call',params,config,scope).stuck,false);
});
test('global no-progress breaker still applies',()=>{
 const s={};const params={command:'same'};
 for(let i=0;i<30;i++){record(s,'exec',params,String(i),config,scope);outcome(s,{toolName:'exec',toolParams:params,toolCallId:String(i),config,runId:'run-a',result:{content:[{type:'text',text:'same output'}],details:{status:'completed',exitCode:0,aggregated:'same output'}}});}
 assert.equal(detect(s,'exec',params,config,scope).stuck,true);
 assert.notEqual(detect(s,'exec',params,config,scope).detector,'unknown_tool_repeat');
});
