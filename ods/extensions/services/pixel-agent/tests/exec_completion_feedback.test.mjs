import test from 'node:test';
import assert from 'node:assert/strict';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';

function exercise({deferred=false, status='completed', exitCode=0, session=false, variant=''}={}) {
  const guard=createToolLoopGuard();
  const context={agentId:'pixel',runId:'run',sessionId:'owner-session',sessionKey:'owner-key',toolCallId:'exec-call'};
  guard.observeRun(context,'pixel',{prompt:'Run the existing Python unit tests and report the result.'});
  const toolName=deferred?'tool_call':'exec', args={command:'python3 -m unittest',workdir:'/workspace/project'};
  const params=deferred?{id:'openclaw:core:exec',args}:args;
  const before=guard.beforeToolCall({toolName,params},{...context,toolName});
  assert.notEqual(before?.block,true);
  const actual=structuredClone(before?.params??params);
  const inner={content:[{type:'text',text:'Ran 22 tests. OK'}],details:{status,exitCode,...(session?{sessionId:'real-session'}:{})}};
  const envelope={tool:{id:'openclaw:core:exec',name:'exec',source:'openclaw',sourceName:'core'},result:inner};
  const result=deferred?{details:envelope,content:[{type:'text',text:JSON.stringify(envelope)}]}:inner;
  const afterContext={...context,toolName};
  if(variant==='wrong-command') (deferred?actual.args:actual).command='python3 another.py';
  if(variant==='wrong-session') afterContext.sessionId='other-session';
  if(variant==='wrong-run') afterContext.runId='other-run';
  if(variant!=='no-after') guard.afterToolCall({toolName,params:actual,result},afterContext);
  const original={role:'toolResult',toolName,toolCallId:context.toolCallId,...structuredClone(result)};
  const persisted=deferred?original.details.result:original;
  if(variant==='changed-exit') persisted.details.exitCode=1;
  if(variant==='became-running') persisted.details.status='running';
  if(variant==='added-session') persisted.details.sessionId='new-session';
  const persistContext={...context,toolName};
  if(variant==='persist-session') persistContext.sessionId='other-session';
  if(variant==='persist-call') original.toolCallId='other-call';
  const projected=guard.toolResultPersist({toolName,toolCallId:context.toolCallId,message:original},persistContext)?.message??original;
  return {original,projected,text:projected.content.filter(x=>x.type==='text').map(x=>x.text).join('\n')};
}

for(const deferred of [false,true]) {
  for(const exitCode of [0,1,2,255]) test(`completed execution exposes exact exit ${exitCode}, deferred=${deferred}`,()=>{
    const {original,projected,text}=exercise({deferred,exitCode});
    assert.match(text,new RegExp(`Exec returned completed with exit code ${exitCode}\\.`));
    assert.match(text,/no background session ID/);
    assert.match(text,/do not invent a session ID or poll a PID/);
    assert.equal(projected.details,original.details);
    assert.deepEqual(projected.content.slice(0,original.content.length),original.content);
    assert.doesNotMatch(text,/all tests passed|processes stopped|quiescent/);
  });
  for(const variant of ['wrong-command','wrong-session','wrong-run','no-after','changed-exit','became-running','added-session','persist-session','persist-call']) test(`unbound receipt has no execution advice: ${variant}, deferred=${deferred}`,()=>{
    assert.doesNotMatch(exercise({deferred,variant}).text,/\[ODS Pixel execution\]/);
  });
  for(const options of [{status:'running'},{status:'error'},{session:true},{exitCode:null},{exitCode:1.5},{exitCode:-1},{exitCode:256}]) test(`nonterminal or session receipt retained: ${JSON.stringify(options)}, deferred=${deferred}`,()=>{
    assert.doesNotMatch(exercise({deferred,...options}).text,/\[ODS Pixel execution\]/);
  });
}
