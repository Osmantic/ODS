import test from 'node:test';
import assert from 'node:assert/strict';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';

function exercise({deferred=false, status='completed', exitCode=0, session=false, variant='', wrapped=false, eventError=false}={}) {
  const guard=createToolLoopGuard(wrapped?{execControl:{prepare:(_run,command)=>'wrapper '+command}}:{});
  const context={agentId:'pixel',runId:'run',sessionId:'owner-session',sessionKey:'owner-key',toolCallId:'exec-call'};
  guard.observeRun(context,'pixel',{prompt:'Run the existing Python unit tests and report the result.'});
  const toolName=deferred?'tool_call':'exec', args={command:'python3 -m unittest',workdir:'/workspace/project'};
  const params=deferred?{id:'openclaw:core:exec',args}:args;
  const before=guard.beforeToolCall({toolName,params},{...context,toolName});
  assert.notEqual(before?.block,true);
  // Match the pinned SDK's direct top-level merge, including normalized keys
  // that the hook omits rather than explicitly overriding.
  const actual=structuredClone(deferred?(before?.params??params):{...params,...before?.params});
  const inner={content:[{type:'text',text:'Ran 22 tests. OK'}],details:{status,exitCode,...(session?{sessionId:'real-session'}:{})}};
  const envelope={tool:{id:'openclaw:core:exec',name:'exec',source:'openclaw',sourceName:'core'},result:inner};
  const result=deferred?{details:envelope,content:[{type:'text',text:JSON.stringify(envelope)}]}:inner;
  const afterContext={...context,toolName};
  if(variant==='wrong-command') (deferred?actual.args:actual).command='python3 another.py';
  if(variant==='wrong-workdir') (deferred?actual.args:actual).workdir='/workspace/unrelated';
  if(variant==='wrong-session') afterContext.sessionId='other-session';
  if(variant==='wrong-run') afterContext.runId='other-run';
  if(variant!=='no-after') guard.afterToolCall({toolName,params:actual,result,...(eventError?{error:'Command exited with code '+exitCode}:{})},afterContext);
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
  for(const variant of ['wrong-command','wrong-workdir','wrong-session','wrong-run','no-after','changed-exit','became-running','added-session','persist-session','persist-call']) test(`unbound receipt has no execution advice: ${variant}, deferred=${deferred}`,()=>{
    assert.doesNotMatch(exercise({deferred,variant}).text,/\[ODS Pixel execution\]/);
  });
  for(const options of [{status:'running'},{status:'error'},{session:true},{exitCode:null},{exitCode:1.5},{exitCode:-1},{exitCode:256}]) test(`nonterminal or session receipt retained: ${JSON.stringify(options)}, deferred=${deferred}`,()=>{
    assert.doesNotMatch(exercise({deferred,...options}).text,/\[ODS Pixel execution\]/);
  });
}

for(const deferred of [false,true]) for(const wrapped of [false,true]) {
  test(`SDK nonzero error remains completed, deferred=${deferred}, wrapped=${wrapped}`,()=>{
    assert.match(exercise({deferred,wrapped,exitCode:1,eventError:true}).text,/Exec returned completed with exit code 1/);
  });
  for(const options of [{exitCode:0},{status:'running',exitCode:1},{status:'error',exitCode:1},{exitCode:1.5},{exitCode:256},{exitCode:1,variant:'wrong-command'},{exitCode:1,variant:'wrong-workdir'}])test(`SDK error cannot manufacture completion: ${JSON.stringify(options)}, deferred=${deferred}, wrapped=${wrapped}`,()=>{
    assert.doesNotMatch(exercise({deferred,wrapped,eventError:true,...options}).text,/\[ODS Pixel execution\]/);
  });
}

test('identical execution advice is not repeated on every completed exec',()=>{
  const guard=createToolLoopGuard();
  const context={agentId:'pixel',runId:'run',sessionId:'owner-session',sessionKey:'owner-key'};
  guard.observeRun(context,'pixel',{prompt:'Run the existing Python unit tests and report the result.'});
  const run=(toolCallId,exitCode)=>{
    const ctx={...context,toolName:'exec',toolCallId},params={command:'python3 -m unittest',workdir:'/workspace/project'};
    const before=guard.beforeToolCall({toolName:'exec',params,toolCallId},ctx);
    const result={content:[{type:'text',text:'Ran 22 tests.'}],details:{status:'completed',exitCode}};
    guard.afterToolCall({toolName:'exec',params:{...params,...before?.params},toolCallId,result},ctx);
    const message={role:'toolResult',toolName:'exec',toolCallId,...structuredClone(result)};
    const projected=guard.toolResultPersist({toolName:'exec',toolCallId,message},ctx)?.message??message;
    return projected.content.filter(x=>x.type==='text').map(x=>x.text).join('\n');
  };
  assert.match(run('exec-1',0),/Exec returned completed with exit code 0\./);
  assert.doesNotMatch(run('exec-2',0),/\[ODS Pixel execution\]/);
  // Different facts are new advice and are always delivered.
  assert.match(run('exec-3',1),/Exec returned completed with exit code 1\./);
});
