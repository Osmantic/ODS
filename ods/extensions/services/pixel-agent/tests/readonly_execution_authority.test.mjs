import test from 'node:test';
import assert from 'node:assert/strict';
import { createReadonlyExecutionAuthority } from '../plugin/readonly-execution-authority.mjs';

const scope = Object.freeze({
  agentId: 'pixel',
  runId: 'run-1',
  sessionId: 'session-1',
  sessionKey: 'session-key-1'
});

function makeEvent(overrides = {}) {
  return {
    ...scope,
    toolName: 'exec',
    toolCallId: 'call-1',
    params: { cmd: 'ls', args: ['-la'] },
    ...overrides
  };
}

function makeContext(overrides = {}) {
  return {
    ...scope,
    toolName: 'exec',
    toolCallId: 'call-1',
    ...overrides
  };
}

test('outsider consume without ALS returns false', () => {
  const auth = createReadonlyExecutionAuthority();
  assert.equal(auth.consume(makeEvent(), makeContext()), false);
});

test('forged prefix/callid/same params without ALS returns false', () => {
  const auth = createReadonlyExecutionAuthority();
  assert.equal(auth.consume(makeEvent({ toolName: 'exec:forged' }), makeContext()), false);
  assert.equal(auth.consume(makeEvent({ toolCallId: 'forged' }), makeContext()), false);
  assert.equal(auth.consume(makeEvent(), makeContext()), false);
});

test('exact consume once then second false', async () => {
  const auth = createReadonlyExecutionAuthority();
  await auth.run(scope, 'exec', 'call-1', { cmd: 'ls', args: ['-la'] }, async () => {
    assert.equal(auth.consume(makeEvent(), makeContext()), true);
    assert.equal(auth.consume(makeEvent(), makeContext()), false);
  });
});

test('wrong scope/tool/callid/params returns false', async () => {
  const auth = createReadonlyExecutionAuthority();
  await auth.run(scope, 'exec', 'call-1', { cmd: 'ls', args: ['-la'] }, async () => {
    assert.equal(auth.consume(makeEvent({ agentId: 'other' }), makeContext()), false);
    assert.equal(auth.consume(makeEvent({ runId: 'other' }), makeContext()), false);
    assert.equal(auth.consume(makeEvent({ sessionId: 'other' }), makeContext()), false);
    assert.equal(auth.consume(makeEvent({ sessionKey: 'other' }), makeContext()), false);
    assert.equal(auth.consume(makeEvent({ toolName: 'process' }), makeContext()), false);
    assert.equal(auth.consume(makeEvent({ toolCallId: 'other' }), makeContext()), false);
    assert.equal(auth.consume(makeEvent({ params: { cmd: 'rm' } }), makeContext()), false);
    assert.equal(auth.consume(makeEvent(), makeContext({ toolCallId: 'other' })), false);
    assert.equal(auth.consume(makeEvent(), makeContext({ toolName: 'process' })), false);
    assert.equal(auth.consume(makeEvent(), makeContext()), true);
  });
});

test('provider argument mutation after run start does not widen authority', async () => {
  const auth = createReadonlyExecutionAuthority();
  const params = { cmd: 'ls', args: ['-la'] };
  await auth.run(scope, 'exec', 'call-1', params, async () => {
    params.cmd = 'rm';
    params.args.push('-rf');
    assert.equal(auth.consume(makeEvent({ params: { cmd: 'rm', args: ['-la', '-rf'] } }), makeContext()), false);
    assert.equal(auth.consume(makeEvent(), makeContext()), true);
  });
});

test('awaited promise identity preserved', async () => {
  const auth = createReadonlyExecutionAuthority();
  const marker = { ok: true };
  const result = await auth.run(scope, 'exec', 'call-1', { cmd: 'ls' }, async () => marker);
  assert.equal(result, marker);
});

test('concurrent two scopes isolated', async () => {
  const auth = createReadonlyExecutionAuthority();
  const scopeB = { agentId: 'pixel', runId: 'run-2', sessionId: 'session-2', sessionKey: 'session-key-2' };
  const results = await Promise.all([
    auth.run(scope, 'exec', 'call-1', { cmd: 'ls' }, async () => {
      await new Promise((r) => setTimeout(r, 10));
      return auth.consume(makeEvent({params:{cmd:'ls'}}), makeContext());
    }),
    auth.run(scopeB, 'process', 'call-2', { cmd: 'ps' }, async () => {
      await new Promise((r) => setTimeout(r, 5));
      return auth.consume(
        makeEvent({ ...scopeB, toolName: 'process', toolCallId: 'call-2', params: { cmd: 'ps' } }),
        makeContext({ ...scopeB, toolName: 'process', toolCallId: 'call-2' })
      );
    })
  ]);
  assert.deepEqual(results, [true, true]);
});

test('ended descendant timer cannot consume even with inherited store', async () => {
  const auth = createReadonlyExecutionAuthority();
  let timerResult;
  await auth.run(scope, 'exec', 'call-1', { cmd: 'ls' }, async () => {
    setTimeout(() => {
      timerResult = auth.consume(makeEvent({params:{cmd:'ls'}}), makeContext());
    }, 20);
  });
  await new Promise((r) => setTimeout(r, 40));
  assert.equal(timerResult, false);
});

test('callback throw revokes authority', async () => {
  const auth = createReadonlyExecutionAuthority();
  let captured;
  await assert.rejects(
    auth.run(scope, 'exec', 'call-1', { cmd: 'ls' }, async () => {
      captured = () => auth.consume(makeEvent(), makeContext());
      throw new Error('boom');
    }),
    /boom/
  );
  assert.equal(captured(), false);
});

test('nested run rejected', async () => {
  const auth = createReadonlyExecutionAuthority();
  await auth.run(scope, 'exec', 'call-1', { cmd: 'ls' }, async () => {
    await assert.rejects(
      auth.run(scope, 'process', 'call-2', { cmd: 'ps' }, async () => {}),
      /nested/
    );
  });
});

test('invalid scope and params reject', async () => {
  const auth = createReadonlyExecutionAuthority();
  await assert.rejects(auth.run({}, 'exec', 'call-1', { cmd: 'ls' }, async () => {}), /scope/);
  await assert.rejects(auth.run(scope, 'exec', '', { cmd: 'ls' }, async () => {}), /toolCallId/);
  await assert.rejects(auth.run(scope, 'exec', 'call-1', null, async () => {}), /params/);
  await assert.rejects(auth.run(scope, 'exec', 'call-1', { fn: () => {} }, async () => {}), /params/);
  await assert.rejects(auth.run(scope, 'exec', 'call-1', { u: undefined }, async () => {}), /params/);
  await assert.rejects(auth.run(scope, 'exec', 'call-1', { d: new Date() }, async () => {}), /params/);
});

test('context/event toolCallId mismatch returns false', async () => {
  const auth = createReadonlyExecutionAuthority();
  await auth.run(scope, 'exec', 'call-1', { cmd: 'ls',args:['-la'] }, async () => {
    assert.equal(auth.consume(makeEvent({ toolCallId: 'call-1' }), makeContext({ toolCallId: 'call-2' })), false);
    assert.equal(auth.consume(makeEvent({ toolCallId: 'call-2' }), makeContext({ toolCallId: 'call-1' })), false);
    assert.equal(auth.consume(makeEvent(), makeContext()), true);
  });
});

test('installed SDK event shape binds through full context and survives await', async () => {
  const auth=createReadonlyExecutionAuthority();
  const params={command:'python3 -c "print(2+2)"',workdir:'/agent'};
  await auth.run(scope,'exec','call-1',params,async()=>{
    await Promise.resolve();
    assert.equal(auth.consume({toolName:'exec',runId:scope.runId,toolCallId:'call-1',params},makeContext()),true);
  });
});

test('context identity is required, not merely copied event fields', async () => {
  for(const key of ['agentId','runId','sessionId','sessionKey']) {
    const auth=createReadonlyExecutionAuthority();
    await auth.run(scope,'exec','call-1',makeEvent().params,async()=>{
      assert.equal(auth.consume(makeEvent(),makeContext({[key]:'wrong'})),false,key);
      const missing=makeContext();delete missing[key];
      assert.equal(auth.consume(makeEvent(),missing),false,key);
      assert.equal(auth.consume(makeEvent(),makeContext()),true);
    });
  }
});

test('non-JSON values and getters fail without invoking user code', async () => {
  const auth=createReadonlyExecutionAuthority();let getterCalls=0;
  const getter={get value(){getterCalls++;return 'unsafe';}};
  const cyclic={};cyclic.self=cyclic;
  const sparse=[];sparse.length=2;
  for(const params of [getter,cyclic,{value:NaN},{value:Infinity},{value:1n},{value:new Map()}, {value:sparse}])
    await assert.rejects(auth.run(scope,'exec','call-1',params,async()=>{}),/params/);
  assert.equal(getterCalls,0);
  await assert.rejects(auth.run({...scope,agentId:'other'},'exec','call-1',{},async()=>{}),/pixel/);
});

test('authority instances cannot consume one another', async () => {
  const a=createReadonlyExecutionAuthority(),b=createReadonlyExecutionAuthority();
  await a.run(scope,'exec','call-1',makeEvent().params,async()=>{
    assert.equal(b.consume(makeEvent(),makeContext()),false);
    assert.equal(a.consume(makeEvent(),makeContext()),true);
  });
});
