import test from 'node:test';
import assert from 'node:assert/strict';
import {createLayaBatchTool,createLayaBatchAdmission,normalizeLayaBatch,LAYA_BATCH_TOOL} from '../plugin/laya-batch.mjs';
import {LayaServiceError} from '../plugin/laya-client.mjs';
import {createLayaBatchExecution} from '../plugin/laya-batch-execution.mjs';
import {resolve} from 'node:path';
import {displayForActivity} from '../plugin/activity-display.mjs';

const request=()=>({source:{path:'tickets.csv',textColumn:'text',idColumn:'id'},outputDirectory:'reports',
  questions:[{id:'category',type:'choice',instructions:'Choose the primary topic',choices:[
    {id:'billing',description:'payments and refunds'},{id:'other',description:'anything else'}]}]});
const context={agentId:'pixel',sessionId:'session',sessionKey:'key',runId:'run',toolCallId:'call'};

function fixture({count=65,disabled=false,onDecide,onWrite,invalidatePreview}={}) {
  const admission=createLayaBatchAdmission(), calls=[],writes=[];
  let active=!disabled;
  const client={decide:async(batch,signal)=>{
    calls.push(batch);
    await onDecide?.({calls,signal,disable:()=>{active=false;}});
    return {items:batch.items.map(({id})=>({id,checkpoint:'english',answers:{category:{type:'choice',choice:'billing',
      probabilities:{billing:0.8,other:0.2},confidence:0.7,answerConfidence:0.8}}}))};
  }};
  const execution={scopeForContext:scope=>scope,runHelper:async(_scope,payload)=>{
    if (payload.operation==='read') return {status:'succeeded',source:{path:'tickets.csv',bytes:1000,sha256:'a'.repeat(64),
      items:Array.from({length:count},(_,i)=>({id:'r'+(i+1),sourceId:'id-'+i,text:'Refund for duplicate payment'}))}};
    writes.push(payload);
    await onWrite?.(payload);
    return {status:'succeeded',readbackVerified:true,overwritten:false,
      outputs:['report.csv','decisions.json'].map(name=>({path:'reports/'+payload.generation+'/'+name,bytes:100,sha256:'b'.repeat(64)}))};
  }};
  const tool=createLayaBatchTool(context,{resolveClient:()=>active?client:null,admission,execution,invalidatePreview});
  return {tool,calls,writes,admission,async run(signal){
    admission.before({toolName:LAYA_BATCH_TOOL,params:request()},context);
    return tool.execute('call',request(),signal);
  }};
}

test('65 rows use shared questions in 3 bounded calls and one verified report',async()=>{
  const f=fixture(); const result=await f.run();
  assert.equal(result.isError,undefined);
  assert.deepEqual(f.calls.map(x=>x.items.length),[32,32,1]);
  assert.equal(f.writes.length,1);
  assert.deepEqual(f.writes[0].report.items.map(x=>x.sourceId),Array.from({length:65},(_,i)=>'id-'+i));
  const visible=JSON.parse(result.content[0].text);
  assert.equal(visible.counts.category.billing,65);
  assert.equal(visible.accuracyVerified,false);
  assert.equal(visible.reviewCandidates.length,8);
  assert.ok(result.content[0].text.length<6000);
  assert.deepEqual(f.writes[0].report.items[64].answers.category.probabilities,{billing:0.8,other:0.2});
});

test('failure on a later batch returns no partial decisions and performs no write or retry',async()=>{
  const f=fixture({onDecide:({calls})=>{if(calls.length===2) throw new LayaServiceError('unavailable','offline',true);}});
  const result=await f.run();
  assert.equal(result.isError,true);assert.equal(result.details.status,'unavailable');
  assert.equal(f.calls.length,2);assert.equal(f.writes.length,0);
  assert.doesNotMatch(result.content[0].text,/probabilities|id-0/);
});

test('disable or cancellation between batches stops processing and never writes',async()=>{
  for (const cancel of [true,false]) {
    const controller=new AbortController();
    const f=fixture({onDecide:({disable})=>{if(cancel) controller.abort(); else disable();}});
    const result=await f.run(controller.signal);
    assert.equal(result.isError,true);assert.equal(f.calls.length,1);assert.equal(f.writes.length,0);
  }
});

test('disabled, unadmitted and mismatched calls cannot touch files',async()=>{
  const off=fixture({disabled:true});assert.equal((await off.run()).details.status,'laya-disabled');
  const f=fixture();assert.equal((await f.tool.execute('call',request())).details.status,'batch-call-unbound');
  f.admission.before({toolName:LAYA_BATCH_TOOL,params:request()},context);
  assert.equal((await f.tool.execute('call',{...request(),outputDirectory:'elsewhere'})).details.status,'batch-call-unbound');
  assert.equal(f.calls.length,0);assert.equal(f.writes.length,0);
});

test('deferred admission binds exact session, arguments and a single execution',()=>{
  const admission=createLayaBatchAdmission();
  admission.before({toolName:'tool_call',params:{id:'openclaw:pixel-ods:'+LAYA_BATCH_TOOL,args:request()}},context);
  const id='tool_search_code:call:'+LAYA_BATCH_TOOL+':1';
  assert.throws(()=>admission.take(id,request(),{...context,sessionId:'other'}),/unbound/);
  assert.deepEqual(admission.take(id,request(),context),context);
  assert.throws(()=>admission.take(id,request(),context),/unbound/);
});

test('invalid paths, fields and colliding CSV columns fail before execution',()=>{
  for (const value of [
    {...request(),source:{...request().source,path:'../secret.csv'}},
    {...request(),source:{...request().source,path:'https://example.com/input.csv'}},
    {...request(),source:{...request().source,path:'input.exe'}},
    {...request(),source:{...request().source,textColumn:''}},
    {...request(),questions:[{...request().questions[0],id:'id'}]},
    {...request(),questions:[request().questions[0],request().questions[0]]},
    {...request(),command:'execute'},
  ]) assert.throws(()=>normalizeLayaBatch(value));
});

test('unexpected implementation failures are not disguised as service failure',async()=>{
  const f=fixture({onDecide:()=>{throw new Error('bug');}});
  await assert.rejects(f.run(),/bug/);
});

test('a run retired during inference cannot publish a report',async()=>{
  const f=fixture({count:2,invalidatePreview:scope=>{
    assert.equal(scope.runId,'run');return false;
  }});
  const result=await f.run();
  assert.equal(result.details.status,'batch-run-no-longer-active');
  assert.equal(f.calls.length,1);assert.equal(f.writes.length,0);
});

test('guessed tool fields return specific schema guidance without reading any files',async()=>{
  const f=fixture();
  const result=await f.tool.execute('call',{path:'/home/input.csv',columns:['id','text']});
  assert.equal(result.details.status,'invalid-batch-request');
  assert.match(result.content[0].text,/source:\{path,textColumn,idColumn\?\}/);
  assert.match(result.content[0].text,/workspace-relative/);
  assert.equal(f.calls.length,0);assert.equal(f.writes.length,0);
});

test('misnested checkpoint and language explain repair while preserving strict admission',async()=>{
  const f=fixture({count:2});
  const malformed=request();
  Object.assign(malformed.questions[0],{checkpoint:'english',language:'en'});
  f.admission.before({toolName:LAYA_BATCH_TOOL,params:malformed},context);
  const refused=await f.tool.execute('call',malformed);
  assert.equal(refused.details.status,'invalid_request');
  assert.match(refused.content[0].text,/checkpoint, language and contextTokens belong at the top level/);
  assert.match(refused.content[0].text,/Preserve requested values/);
  assert.equal(f.calls.length,0);assert.equal(f.writes.length,0);
  const repaired={...request(),checkpoint:'english',language:'en'};
  // Repair alone cannot reuse rejected admission or invoke the provider.
  assert.equal((await f.tool.execute('call',repaired)).details.status,'batch-call-unbound');
  f.admission.before({toolName:LAYA_BATCH_TOOL,params:repaired},context);
  const completed=await f.tool.execute('call',repaired);
  assert.equal(completed.isError,undefined);
  assert.equal(f.calls.length,1);assert.equal(f.calls[0].checkpoint,'english');assert.equal(f.calls[0].language,'en');
});

test('invalid extra question identifies its field so repair preserves the dataset paths',async()=>{
  const f=fixture({count:2});
  const malformed=request();
  malformed.questions.push({id:'format',type:'choice',instructions:'Output format confirmation.',
    choices:[{id:'csv',description:'CSV report'}]});
  f.admission.before({toolName:LAYA_BATCH_TOOL,params:malformed},context);
  const refused=await f.tool.execute('call',malformed);
  assert.equal(refused.details.status,'invalid_request');
  assert.match(refused.content[0].text,/questions\.format\.choices must contain 2–20 entries/);
  assert.match(refused.content[0].text,/preserve unrelated source and output paths/);
  assert.equal(f.calls.length,0);assert.equal(f.writes.length,0);
  const repaired=request();
  assert.equal((await f.tool.execute('call',repaired)).details.status,'batch-call-unbound');
  f.admission.before({toolName:LAYA_BATCH_TOOL,params:repaired},context);
  assert.equal((await f.tool.execute('call',repaired)).isError,undefined);
  assert.equal(f.calls.length,1);assert.equal(f.writes.length,1);
  assert.equal(f.writes[0].source.path,repaired.source.path);
});

test('SDK dataset execution binds workspace, model and unchanged policy',async()=>{
  let config={agents:{list:[{id:'pixel',workspace:resolve('workspace'),tools:{exec:{host:'gateway'}}}]}},options;
  const factory={...context,workspaceDir:config.agents.list[0].workspace,activeModel:{provider:'local',modelId:'fixture'}};
  const adapter=createLayaBatchExecution({readConfig:()=>config,resolveSandbox:async()=>null,
    helperSource:()=>'',execControl:()=>({prepare:(_id,command)=>command}),
    createTools:args=>{options=args;return [{name:'exec',execute:async()=>({details:{status:'completed',exitCode:0,aggregated:'{"status":"succeeded"}'}})}];}});
  assert.throws(()=>adapter.scopeForContext(context,{...factory,workspaceDir:resolve('other')}),/scope-unavailable/);
  const scope=adapter.scopeForContext(context,factory);
  await adapter.runHelper(scope,{operation:'read',source:request().source});
  assert.equal(options.config,config);assert.equal(options.modelProvider,'local');assert.equal(options.runId,context.runId);
  config={...config,tools:{deny:['exec']}};
  await assert.rejects(adapter.runHelper(scope,{}),/runtime-changed/);
});

test('cancellation kills and observes only the owned SDK process without claiming output',async()=>{
  const abort=new AbortController(),actions=[];
  const config={agents:{list:[{id:'pixel',workspace:resolve('workspace'),tools:{exec:{host:'gateway'}}}]}};
  const adapter=createLayaBatchExecution({readConfig:()=>config,resolveSandbox:async()=>null,helperSource:()=>'',
    execControl:()=>({prepare:(_id,x)=>x,signal:()=>actions.push('signal')}),createTools:()=>[
      {name:'exec',execute:async()=>{abort.abort();return {details:{status:'running',sessionId:'owned'}};}},
      {name:'process',execute:async(_id,args)=>{assert.equal(args.sessionId,'owned');actions.push(args.action);
        return {details:{status:'failed',sessionId:'owned',exitCode:143}};}},
    ]});
  const scope=adapter.scopeForContext(context,{...context,workspaceDir:config.agents.list[0].workspace});
  await assert.rejects(adapter.runHelper(scope,{operation:'read'},abort.signal),/cancelled/);
  assert.deepEqual(actions,['signal','kill','poll']);
});

test('activity presents the dataset action without encoded source/report contents',()=>{
  for(const operation of ['read','write']) {
    const display=displayForActivity({toolName:'exec',params:{command:'encoded private data'}},
      {toolName:'exec',toolCallId:'ods-laya-test-'+operation});
    assert.equal(display.detail,null);assert.match(display.label,/dataset|report/);
  }
});

test('nested SDK outcomes close their exact admission entry, including thrown failures',async()=>{
  for (const throws of [false,true]) {
    const active=new Set(),observed=[];
    const config={agents:{list:[{id:'pixel',workspace:resolve('workspace'),tools:{exec:{host:'gateway'}}}]}};
    const adapter=createLayaBatchExecution({readConfig:()=>config,resolveSandbox:async()=>null,helperSource:()=>'',
      execControl:()=>({prepare:(_id,command)=>command}),createTools:()=>[
        {name:'exec',execute:async id=>{
          // Pinned SDK calls before_tool_call; its outer harness normally owns after_tool_call.
          active.add(id);
          if (throws) throw new Error('execution failed');
          return {details:{status:'completed',exitCode:0,aggregated:'{"status":"succeeded"}'}};
        }},
      ],onToolResult:(event,scope)=>{
        assert.equal(event.toolCallId,scope.toolCallId);assert.equal(scope.runId,context.runId);
        assert.equal(active.delete(event.toolCallId),true);observed.push(event);
      }});
    const scope=adapter.scopeForContext(context,{...context,workspaceDir:config.agents.list[0].workspace});
    if (throws) await assert.rejects(adapter.runHelper(scope,{operation:'read'}),/execution failed/);
    else await adapter.runHelper(scope,{operation:'read'});
    assert.equal(active.size,0);assert.equal(observed.length,1);
    assert.equal(Boolean(observed[0].error),throws);
  }
});
