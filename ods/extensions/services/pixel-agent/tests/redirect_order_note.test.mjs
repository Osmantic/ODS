import test from 'node:test';
import assert from 'node:assert/strict';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {REDIRECT_ORDER_NOTE} from '../plugin/shell-redirect-order.mjs';

// tower1 7402eb38, 2026-09-25 11:07Z coding journey (Qwen3.5-27B), verbatim.
const FLEET_COMMAND = 'cd fleet-qualification-489210351f87-coding && mkdir -p public && python3 -m unittest -v 2>&1 > public/test-results.txt';

// Replays one exec through before/after/persist hooks and returns the text the
// model sees. The fleet receipt: unittest output on stderr reached the exec
// result while public/test-results.txt stayed empty.
// Production always wraps exec for cancellation; the unwrapped variant keeps
// the transport fixture of exec_completion_feedback.test.mjs.
function exercise({command=FLEET_COMMAND, deferred=false, status='completed', exitCode=0, session=false,
  wrapped=true, workdir=wrapped?'/workspace':'/workspace/project', variant='', guard, runId='run', toolCallId='exec-call'}={}) {
  guard ??= createToolLoopGuard(wrapped?{execControl:{prepare:(_run,value)=>'wrapper '+value}}:{});
  const context={agentId:'pixel',runId,sessionId:'owner-session',sessionKey:'owner-key',toolCallId};
  guard.observeRun(context,'pixel',{prompt:'Build the tested coding project and publish its test output in public/.'});
  const toolName=deferred?'tool_call':'exec', args={command,workdir};
  const params=deferred?{id:'openclaw:core:exec',args}:args;
  const before=guard.beforeToolCall({toolName,params},{...context,toolName});
  assert.notEqual(before?.block,true,before?.blockReason);
  const actual=structuredClone(deferred?(before?.params??params):{...params,...before?.params});
  const inner={content:[{type:'text',text:'test_totals (test_app.Tests.test_totals) ... ok\n\nRan 29 tests in 0.004s\n\nOK'}],
    details:{status,exitCode,...(session?{sessionId:'real-session'}:{})}};
  const envelope={tool:{id:'openclaw:core:exec',name:'exec',source:'openclaw',sourceName:'core'},result:inner};
  const result=deferred?{details:envelope,content:[{type:'text',text:JSON.stringify(envelope)}]}:inner;
  const afterContext={...context,toolName};
  if(variant==='wrong-run') afterContext.runId='other-run';
  if(variant==='wrong-command') (deferred?actual.args:actual).command='python3 -m unittest -v > public/test-results.txt 2>&1';
  if(variant!=='no-after') guard.afterToolCall({toolName,params:actual,result},afterContext);
  const original={role:'toolResult',toolName,toolCallId,...structuredClone(result)};
  const persisted=deferred?original.details.result:original;
  if(variant==='became-running') persisted.details.status='running';
  if(variant==='added-session') persisted.details.sessionId='new-session';
  const persistContext={...context,toolName};
  if(variant==='persist-session') persistContext.sessionId='other-session';
  const projected=guard.toolResultPersist({toolName,toolCallId,message:original},persistContext)?.message??original;
  return {original,projected,texts:projected.content.filter(x=>x.type==='text').map(x=>x.text)};
}

for(const deferred of [false,true]) for(const wrapped of [false,true]) {
  test(`fleet replay: exact tower1 command gets the redirect-order note, deferred=${deferred}, wrapped=${wrapped}`,()=>{
    const {original,projected,texts}=exercise({deferred,wrapped});
    assert.equal(texts.filter(text=>text===REDIRECT_ORDER_NOTE).length,1);
    assert.ok(texts.some(text=>/Exec returned completed with exit code 0\./.test(text)));
    // Informational: the receipt itself is unchanged and nothing is rewritten.
    assert.deepEqual(projected.content.slice(0,original.content.length),original.content);
    assert.equal(projected.details,original.details);
  });
}

test('the command is neither blocked nor rewritten',()=>{
  const guard=createToolLoopGuard();
  const context={agentId:'pixel',runId:'run',sessionId:'owner-session',sessionKey:'owner-key',toolCallId:'exec-call',toolName:'exec'};
  guard.observeRun(context,'pixel',{prompt:'Build the tested coding project and publish its test output in public/.'});
  const before=guard.beforeToolCall({toolName:'exec',params:{command:FLEET_COMMAND,workdir:'/workspace'}},context);
  assert.notEqual(before?.block,true);
  assert.equal(before?.params?.command??FLEET_COMMAND,FLEET_COMMAND);
});

for(const exitCode of [1,2]) test(`failing runs get the note too: exit ${exitCode}`,()=>{
  assert.ok(exercise({exitCode}).texts.includes(REDIRECT_ORDER_NOTE));
});

for(const command of [
  'cd fleet-qualification-489210351f87-coding && mkdir -p public && python3 -m unittest -v > public/test-results.txt 2>&1',
  'cd fleet-qualification-489210351f87-coding && python3 -m unittest -v 2>&1 | tee public/test-results.txt',
  'cd fleet-qualification-489210351f87-coding && python3 -m unittest -v &> public/test-results.txt',
  "cat > public/run.sh <<'EOF'\npython3 -m unittest -v 2>&1 > public/test-results.txt\nEOF",
  'echo "python3 -m unittest -v 2>&1 > public/test-results.txt"',
]) test(`no note for ${JSON.stringify(command)}`,()=>{
  assert.ok(!exercise({command}).texts.includes(REDIRECT_ORDER_NOTE));
});

for(const options of [{status:'running'},{session:true},{variant:'wrong-run'},{variant:'no-after'},
  {variant:'wrong-command'},{variant:'became-running'},{variant:'added-session'},{variant:'persist-session'}])
  test(`no note without a bound completed foreground receipt: ${JSON.stringify(options)}`,()=>{
    for(const deferred of [false,true]) assert.ok(!exercise({deferred,...options}).texts.includes(REDIRECT_ORDER_NOTE));
  });

test('a repeated mistake in the follow-up turn gets the same fixed note again',()=>{
  const guard=createToolLoopGuard({execControl:{prepare:(_run,value)=>'wrapper '+value}});
  const first=exercise({guard,runId:'create-run',toolCallId:'exec-1'}).texts;
  const second=exercise({guard,runId:'follow-up-run',toolCallId:'exec-2'}).texts;
  assert.ok(first.includes(REDIRECT_ORDER_NOTE));
  assert.ok(second.includes(REDIRECT_ORDER_NOTE));
  assert.equal(first.find(text=>text.startsWith('Note:')),second.find(text=>text.startsWith('Note:')));
});
