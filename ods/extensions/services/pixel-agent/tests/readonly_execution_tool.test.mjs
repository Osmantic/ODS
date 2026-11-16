import test from 'node:test';
import assert from 'node:assert/strict';
import {
  READONLY_EXEC_TOOL,
  normalizeReadonlyExec,
  createReadonlyExecAdmission,
  createReadonlyExecTool,
} from '../plugin/readonly-execution-tool.mjs';

const baseContext = (overrides = {}) => ({
  agentId: 'pixel',
  runId: 'run-1',
  sessionId: 'sess-1',
  sessionKey: 'key-1',
  toolCallId: 'call-1',
  ...overrides,
});

const factory = {agentId: 'pixel', sessionId: 'sess-1', sessionKey: 'key-1'};

function admitDirect(admission, context, params) {
  admission.before({toolName: READONLY_EXEC_TOOL, params}, context, {params});
}

function admitDeferred(admission, context, params, id = READONLY_EXEC_TOOL) {
  admission.before(
    {toolName: 'tool_call', params: {id, args: params}},
    context,
    {params: {id, args: params}},
  );
}

test('normalizeReadonlyExec accepts minimal and full requests', () => {
  assert.deepEqual(normalizeReadonlyExec({command: 'ls'}), {command: 'ls', workdir: '.', timeoutSeconds: 10});
  assert.deepEqual(
    normalizeReadonlyExec({command: 'cat a.txt', workdir: 'sub dir', timeoutSeconds: 30}),
    {command: 'cat a.txt', workdir: 'sub dir', timeoutSeconds: 30},
  );
});

test('command size ceiling is enforced before execution with the grammar-compatible schema',async()=>{
  const admission=createReadonlyExecAdmission();let executions=0;
  const tool=createReadonlyExecTool(factory,{admission,scopeForContext:active=>active,
    execute:async()=>{executions++;return {details:{status:'completed',exitCode:0}};}});
  const valid={command:'x'.repeat(16384)};
  admitDirect(admission,baseContext(),valid);
  assert.equal((await tool.execute('call-1',valid)).details.status,'completed');
  const result=await tool.execute('call-2',{command:'x'.repeat(16385)});
  assert.equal(result.isError,true);assert.equal(result.details.code,'invalid-command');assert.equal(executions,1);
});

test('normalizeReadonlyExec rejects unknown keys, traversal, absolute, backslash, control, bad timeout', () => {
  assert.throws(() => normalizeReadonlyExec({command: 'ls', extra: 1}), /unknown-readonly-exec-field/);
  assert.throws(() => normalizeReadonlyExec({command: 'ls', workdir: '../x'}), /invalid-workdir/);
  assert.throws(() => normalizeReadonlyExec({command: 'ls', workdir: 'a/../b'}), /invalid-workdir/);
  assert.throws(() => normalizeReadonlyExec({command: 'ls', workdir: '/abs'}), /invalid-workdir/);
  assert.throws(() => normalizeReadonlyExec({command: 'ls', workdir: 'a\\b'}), /invalid-workdir/);
  assert.throws(() => normalizeReadonlyExec({command: 'ls', workdir: 'a\u0001b'}), /invalid-workdir/);
  assert.throws(() => normalizeReadonlyExec({command: 'ls', timeoutSeconds: 0}), /invalid-timeout/);
  assert.throws(() => normalizeReadonlyExec({command: 'ls', timeoutSeconds: 61}), /invalid-timeout/);
  assert.throws(() => normalizeReadonlyExec({command: 'ls', timeoutSeconds: 1.5}), /invalid-timeout/);
  assert.throws(() => normalizeReadonlyExec({command: 'a\u0000b'}), /invalid-command/);
  assert.throws(() => normalizeReadonlyExec({command: ''}), /invalid-command/);
  assert.throws(() => normalizeReadonlyExec(null), /invalid-readonly-exec-request/);
});

test('admission records direct tool and take consumes once', () => {
  const admission = createReadonlyExecAdmission();
  const ctx = baseContext();
  admitDirect(admission, ctx, {command: 'ls'});
  const active = admission.take('call-1', normalizeReadonlyExec({command: 'ls'}), factory);
  assert.equal(active.runId, 'run-1');
  assert.throws(() => admission.take('call-1', normalizeReadonlyExec({command: 'ls'}), factory), /readonly-exec-call-unbound/);
});

test('blocked re-admission revokes prior entry', () => {
  const admission = createReadonlyExecAdmission();
  const ctx = baseContext();
  admitDirect(admission, ctx, {command: 'ls'});
  admission.before({toolName: READONLY_EXEC_TOOL, params: {command: 'ls'}}, ctx, {block: true});
  assert.throws(() => admission.take('call-1', normalizeReadonlyExec({command: 'ls'}), factory), /readonly-exec-call-unbound/);
});

test('invalid re-admission revokes prior entry', () => {
  const admission = createReadonlyExecAdmission();
  const ctx = baseContext();
  admitDirect(admission, ctx, {command: 'ls'});
  admitDirect(admission, ctx, {command: 'ls', workdir: '../x'});
  assert.throws(() => admission.take('call-1', normalizeReadonlyExec({command: 'ls'}), factory), /readonly-exec-call-unbound/);
});

test('deferred exact child matches and consumes parent', () => {
  const admission = createReadonlyExecAdmission();
  const ctx = baseContext({toolCallId: 'parent-1'});
  admitDeferred(admission, ctx, {command: 'ls'});
  const childId = `tool_search_code:parent-1:${READONLY_EXEC_TOOL}:1`;
  const active = admission.take(childId, normalizeReadonlyExec({command: 'ls'}), factory);
  assert.equal(active.toolCallId, 'parent-1');
  assert.throws(() => admission.take(childId, normalizeReadonlyExec({command: 'ls'}), factory), /readonly-exec-call-unbound/);
});

test('deferred wrong prefix, wrong params, wrong session rejected', () => {
  const admission = createReadonlyExecAdmission();
  const ctx = baseContext({toolCallId: 'parent-2'});
  admitDeferred(admission, ctx, {command: 'ls'});
  const wrongPrefix = `tool_search_code:other:${READONLY_EXEC_TOOL}:1`;
  assert.throws(() => admission.take(wrongPrefix, normalizeReadonlyExec({command: 'ls'}), factory), /readonly-exec-call-unbound/);
  const rightPrefix = `tool_search_code:parent-2:${READONLY_EXEC_TOOL}:1`;
  assert.throws(() => admission.take(rightPrefix, normalizeReadonlyExec({command: 'pwd'}), factory), /readonly-exec-call-unbound/);
  assert.throws(
    () => admission.take(rightPrefix, normalizeReadonlyExec({command: 'ls'}), {agentId: 'pixel', sessionId: 'sess-2', sessionKey: 'key-1'}),
    /readonly-exec-call-unbound/,
  );
  // Still available after failed attempts.
  const active = admission.take(rightPrefix, normalizeReadonlyExec({command: 'ls'}), factory);
  assert.equal(active.runId, 'run-1');
});

test('deferred child requires positive integer suffix', () => {
  const admission = createReadonlyExecAdmission();
  const ctx = baseContext({toolCallId: 'parent-3'});
  admitDeferred(admission, ctx, {command: 'ls'});
  const bad = `tool_search_code:parent-3:${READONLY_EXEC_TOOL}:0`;
  assert.throws(() => admission.take(bad, normalizeReadonlyExec({command: 'ls'}), factory), /readonly-exec-call-unbound/);
});

test('concurrent calls each consume their own entry', async () => {
  const admission = createReadonlyExecAdmission();
  const ctxA = baseContext({toolCallId: 'call-a'});
  const ctxB = baseContext({toolCallId: 'call-b'});
  admitDirect(admission, ctxA, {command: 'ls'});
  admitDirect(admission, ctxB, {command: 'pwd'});
  const tool = createReadonlyExecTool(baseContext(), {
    admission,
    execute: async (scope, request) => ({details:{ran: request.command, scope}}),
    scopeForContext: active => ({runId: active.runId, toolCallId: active.toolCallId}),
  });
  const [a, b] = await Promise.all([
    tool.execute('call-a', {command: 'ls'}),
    tool.execute('call-b', {command: 'pwd'}),
  ]);
  assert.equal(a.details.ran, 'ls');
  assert.equal(b.details.ran, 'pwd');
  assert.equal(a.details.scope.toolCallId, 'call-a');
  assert.equal(b.details.scope.toolCallId, 'call-b');
});

test('tool returns isError when admission missing', async () => {
  const admission = createReadonlyExecAdmission();
  const tool = createReadonlyExecTool(baseContext(), {
    admission,
    execute: async () => ({ok: true}),
    scopeForContext: () => ({}),
  });
  const result = await tool.execute('call-x', {command: 'ls'});
  assert.equal(result.isError, true);
  assert.equal(result.details.status, 'failed');
  assert.equal(result.details.code, 'readonly-exec-call-unbound');
});

test('tool propagates signal to execute dependency', async () => {
  const admission = createReadonlyExecAdmission();
  const ctx = baseContext({toolCallId: 'call-sig'});
  admitDirect(admission, ctx, {command: 'ls'});
  const controller = new AbortController();
  let seen;
  const tool = createReadonlyExecTool(baseContext(), {
    admission,
    execute: async (scope, request, signal) => { seen = signal; return {ok: true}; },
    scopeForContext: () => ({}),
  });
  await tool.execute('call-sig', {command: 'ls'}, controller.signal);
  assert.equal(seen, controller.signal);
});

test('tool returns sanitized error for unknown keys', async () => {
  const admission = createReadonlyExecAdmission();
  const tool = createReadonlyExecTool(baseContext(), {
    admission,
    execute: async () => ({ok: true}),
    scopeForContext: () => ({}),
  });
  const result = await tool.execute('call-1', {command: 'ls', host: 'evil'});
  assert.equal(result.isError, true);
  assert.equal(result.details.code, 'unknown-readonly-exec-field');
});

test('tool returns null for non-pixel agent', () => {
  const tool = createReadonlyExecTool({agentId: 'other'}, {
    admission: createReadonlyExecAdmission(),
    execute: async () => ({}),
    scopeForContext: () => ({}),
  });
  assert.equal(tool, null);
});

test('after removes direct and deferred parent entries', () => {
  const admission = createReadonlyExecAdmission();
  const direct = baseContext({toolCallId: 'call-d'});
  admitDirect(admission, direct, {command: 'ls'});
  admission.after({}, direct);
  assert.throws(() => admission.take('call-d', normalizeReadonlyExec({command: 'ls'}), factory), /readonly-exec-call-unbound/);

  const parent = baseContext({toolCallId: 'parent-x'});
  admitDeferred(admission, parent, {command: 'ls'});
  admission.after({}, {toolCallId: `tool_search_code:parent-x:${READONLY_EXEC_TOOL}:1`});
  assert.throws(
    () => admission.take(`tool_search_code:parent-x:${READONLY_EXEC_TOOL}:1`, normalizeReadonlyExec({command: 'ls'}), factory),
    /readonly-exec-call-unbound/,
  );
});

test('clear invalidates all pending entries', () => {
  const admission = createReadonlyExecAdmission();
  admitDirect(admission, baseContext({toolCallId: 'c1'}), {command: 'ls'});
  admitDirect(admission, baseContext({toolCallId: 'c2'}), {command: 'pwd'});
  admission.clear();
  assert.throws(() => admission.take('c1', normalizeReadonlyExec({command: 'ls'}), factory), /readonly-exec-call-unbound/);
  assert.throws(() => admission.take('c2', normalizeReadonlyExec({command: 'pwd'}), factory), /readonly-exec-call-unbound/);
});

test('admission ignores non-pixel and incomplete contexts', () => {
  const admission = createReadonlyExecAdmission();
  admission.before({toolName: READONLY_EXEC_TOOL, params: {command: 'ls'}}, baseContext({agentId: 'other'}), {params: {command: 'ls'}});
  assert.throws(() => admission.take('call-1', normalizeReadonlyExec({command: 'ls'}), factory), /readonly-exec-call-unbound/);
  admission.before({toolName: READONLY_EXEC_TOOL, params: {command: 'ls'}}, baseContext({runId: ''}), {params: {command: 'ls'}});
  assert.throws(() => admission.take('call-1', normalizeReadonlyExec({command: 'ls'}), factory), /readonly-exec-call-unbound/);
});

test('deferred openclaw-prefixed id is admitted', () => {
  const admission = createReadonlyExecAdmission();
  const ctx = baseContext({toolCallId: 'parent-oc'});
  admitDeferred(admission, ctx, {command: 'ls'}, 'openclaw:pixel-ods:' + READONLY_EXEC_TOOL);
  const childId = `tool_search_code:parent-oc:${READONLY_EXEC_TOOL}:2`;
  const active = admission.take(childId, normalizeReadonlyExec({command: 'ls'}), factory);
  assert.equal(active.toolCallId, 'parent-oc');
});

test('normalization is idempotent and never invokes getters', () => {
  const normalized = normalizeReadonlyExec({command:'printf 4'});
  assert.deepEqual(normalizeReadonlyExec(normalized),normalized);
  assert.deepEqual(normalizeReadonlyExec({command:'printf 4',workdir:'.'}),normalized);
  let invoked=false;
  assert.throws(()=>normalizeReadonlyExec({get command(){invoked=true;return 'printf 4';}}),
    /invalid-readonly-exec-request/);
  assert.equal(invoked,false);
  assert.throws(()=>normalizeReadonlyExec({command:'printf 4',[Symbol('backend')]:'gateway'}),
    /unknown-readonly-exec-field/);
});

test('an incomplete or changed-tool observation revokes the old call admission', () => {
  for(const change of [{context:{runId:''}},{name:'exec'}]) {
    const admission=createReadonlyExecAdmission();
    admitDirect(admission,baseContext(),{command:'printf 4'});
    admission.before({toolName:change.name??READONLY_EXEC_TOOL,params:{command:'printf 4'}},
      baseContext(change.context),undefined);
    assert.throws(()=>admission.take('call-1',normalizeReadonlyExec({command:'printf 4'}),factory),
      /readonly-exec-call-unbound/);
  }
});

test('the tool preserves unsuccessful SDK receipts without claiming completion', async () => {
  const admission=createReadonlyExecAdmission();
  admitDirect(admission,baseContext(),{command:'exit 42'});
  const receipt={isError:true,details:{status:'failed',exitCode:42,aggregated:'failure'},
    content:[{type:'text',text:'failure'}]};
  const tool=createReadonlyExecTool(factory,{admission,scopeForContext:active=>active,
    execute:async()=>receipt});
  assert.equal(await tool.execute('call-1',{command:'exit 42'}),receipt);
});

test('colliding normalized deferred parents grant no ambiguous admission', () => {
  const admission=createReadonlyExecAdmission();
  for(const id of ['parent?','parent!']) admitDeferred(admission,baseContext({toolCallId:id}),{command:'printf 4'});
  assert.throws(()=>admission.take('tool_search_code:parent_:'+READONLY_EXEC_TOOL+':1',
    normalizeReadonlyExec({command:'printf 4'}),factory),/readonly-exec-call-unbound/);
});

test('effective guard parameters are authoritative, including an invalid replacement', () => {
  const admission=createReadonlyExecAdmission(),ctx=baseContext();
  admission.before({toolName:READONLY_EXEC_TOOL,params:{command:'old'}},ctx,{params:{command:'new'}});
  assert.equal(admission.take('call-1',normalizeReadonlyExec({command:'new'}),factory).runId,'run-1');
  admission.before({toolName:'tool_call',params:{id:READONLY_EXEC_TOOL,args:{command:'old'}}},
    ctx,{params:{id:READONLY_EXEC_TOOL}});
  assert.throws(()=>admission.take('call-1',normalizeReadonlyExec({command:'old'}),factory),/readonly-exec-call-unbound/);
});

test('normalized child cleanup revokes sanitized parent admissions', () => {
  const admission=createReadonlyExecAdmission();
  admitDeferred(admission,baseContext({toolCallId:'parent?'}),{command:'printf 4'});
  admission.after({}, {toolCallId:'tool_search_code:parent_:'+READONLY_EXEC_TOOL+':1'});
  assert.throws(()=>admission.take('parent?',normalizeReadonlyExec({command:'printf 4'}),factory),/readonly-exec-call-unbound/);
});

test('bounded admission retains 128 calls and evicts the oldest on overflow', () => {
  for(const count of [128,129]) {
    const admission=createReadonlyExecAdmission();
    for(let i=0;i<count;i++) admitDirect(admission,baseContext({toolCallId:'id-'+i}),{command:'printf 4'});
    if(count===129) assert.throws(()=>admission.take('id-0',normalizeReadonlyExec({command:'printf 4'}),factory),/readonly-exec-call-unbound/);
    for(let i=count===129?1:0;i<count;i++)
      assert.equal(admission.take('id-'+i,normalizeReadonlyExec({command:'printf 4'}),factory).runId,'run-1');
  }
});
