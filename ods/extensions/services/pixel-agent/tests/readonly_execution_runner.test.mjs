import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createReadonlyExecutionRunner} from '../plugin/readonly-execution-runner.mjs';

const SCOPE = Object.freeze({
  runId: 'run-1',
  sessionId: 'sess-1',
  sessionKey: 'skey-1',
  agentId: 'pixel',
  toolCallId: 'call-1'
});

const deferred = () => {
  let resolve, reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return {promise, resolve, reject};
};

const makeScheduler = () => {
  const timers = [];
  return {
    timers,
    setTimer(callback, ms) {
      const entry = {callback, ms, cleared: false};
      timers.push(entry);
      return {unref() {}, clear() { entry.cleared = true; }};
    },
    clearTimer(handle) { handle?.clear?.(); },
    fireAll() {
      for (const t of timers) if (!t.cleared) t.callback();
    }
  };
};

const makeAuthority = () => {
  const calls = [];
  const authority = {
    calls,
    run(scope, kind, internalId, params, exec) {
      const record = {scope, kind, internalId, params, exec};
      calls.push(record);
      return exec();
    }
  };
  return authority;
};

const makeHandle = (overrides = {}) => {
  const state = {
    cleanupCalls: 0,
    cleanupDeferred: null,
    execDeferred: null,
    stillCurrent: true,
    params: {command: 'printf 4'},
    started: deferred(), cleaning: deferred(), signal: null,
    cleanupImpl: null,
    execImpl: null
  };
  const handle = {
    params: state.params,
    stillCurrent() { return state.stillCurrent; },
    cleanup() {
      state.cleanupCalls++;
      state.cleaning.resolve();
      if (state.cleanupImpl) return state.cleanupImpl();
      if (state.cleanupDeferred) return state.cleanupDeferred.promise;
      return Promise.resolve();
    },
    exec: {
      execute(internalId, params, signal) {
        state.signal = signal; state.started.resolve();
        if (state.execImpl) return state.execImpl(internalId, params, signal);
        if (state.execDeferred) return state.execDeferred.promise;
        return Promise.resolve({details: {status: 'completed', exitCode: 0}});
      }
    }
  };
  Object.assign(handle, overrides);
  return {handle, state};
};

const makeRunner = (opts = {}) => {
  const scheduler = opts.scheduler ?? makeScheduler();
  const authority = opts.authority ?? makeAuthority();
  const afterCalls = [];
  const prepareCalls = [];
  const prepareImpl = opts.prepare ?? (async () => makeHandle().handle);
  const runner = createReadonlyExecutionRunner({
    prepare: async (scope, request) => {
      prepareCalls.push({scope, request});
      return prepareImpl(scope, request);
    },
    authority,
    afterToolCall: async (payload) => { afterCalls.push(payload); },
    clock: () => 1000,
    setTimer: scheduler.setTimer,
    clearTimer: scheduler.clearTimer
  });
  return {runner, scheduler, authority, afterCalls, prepareCalls};
};

const REQUEST = {timeoutSeconds: 5};

test('public cwd projection preserves command output and the original internal SDK receipt', async () => {
  for (const status of ['completed','failed']) {
    const host='/private/bootstrap';
    const receipt=Object.freeze({content:[{type:'text',text:host}],details:Object.freeze({
      status,exitCode:status==='failed'?2:0,cwd:host,durationMs:108,sessionId:'opaque-handle',aggregated:host
    }),isError:status==='failed',structuredContent:{evidence:'untouched'}});
    const {handle,state}=makeHandle({params:{command:'fixture',workdir:host},
      publicWorkdir:Object.freeze({cwd:'/agent/data',workdir:'data'})});
    state.execImpl=async()=>receipt;
    const {runner,afterCalls}=makeRunner({prepare:async()=>handle});
    const result=await runner.execute(SCOPE,REQUEST);
    assert.deepEqual(result,{...receipt,details:{...receipt.details,cwd:'/agent/data',workdir:'data'}});
    assert.equal(result.content,receipt.content);
    assert.equal(result.structuredContent,receipt.structuredContent);
    assert.equal(afterCalls[0].result,receipt);
    assert.equal(receipt.details.cwd,host);
    assert.equal(state.cleanupCalls,1);
  }
});

test('cwd projection does not replace unknown SDK paths or add missing cwd evidence', async () => {
  for (const cwd of [undefined,'/agent','/unrecognized/private/path']) {
    const receipt={details:{status:'completed',exitCode:0,...(cwd===undefined?{}:{cwd})}};
    const {handle,state}=makeHandle({params:{command:'fixture',workdir:'/private/bootstrap'},
      publicWorkdir:{cwd:'/agent/data',workdir:'data'}});
    state.execImpl=async()=>receipt;
    const {runner}=makeRunner({prepare:async()=>handle});
    assert.equal(await runner.execute(SCOPE,REQUEST),receipt);
  }
});

test('unconfirmed preparation failure latches new work and cannot confirm cancellation', async () => {
  const {runner}=makeRunner({prepare:async()=>{throw new Error('provisioning failed after allocation');}});
  await assert.rejects(runner.execute(SCOPE,REQUEST),/readonly-cleanup-unconfirmed/);
  assert.equal(await runner.cancel(SCOPE),false);
  await assert.rejects(runner.execute({...SCOPE,toolCallId:'new'},REQUEST),/readonly-recovery-required/);
});

test('verified preparation rollback releases the job', async () => {
  const {runner}=makeRunner({prepare:async()=>{throw Object.assign(new Error('provisioning failed'),{readonlyCleanupConfirmed:true});}});
  await assert.rejects(runner.execute(SCOPE,REQUEST),/provisioning failed/);
  assert.equal(runner.pendingFor(SCOPE),0);
});

test('partial preparation supplies exact cleanup recovery for later cancellation', async () => {
  let attempts=0;
  const {runner}=makeRunner({prepare:async()=>{throw Object.assign(new Error('partial'),{readonlyRecovery:{
    cleanup:async()=>{if(++attempts===1)throw new Error('Docker temporarily down');}
  }});}});
  await assert.rejects(runner.execute(SCOPE,REQUEST),/readonly-cleanup-unconfirmed/);
  assert.equal(await runner.cancel(SCOPE),true);
  assert.equal(attempts,2);assert.equal(runner.pendingFor(SCOPE),0);
});

test('SDK rejection after deadline preserves the timeout reason', async () => {
  const scheduler=makeScheduler(),{handle,state}=makeHandle();state.execDeferred=deferred();
  const {runner}=makeRunner({scheduler,prepare:async()=>handle});
  const pending=runner.execute(SCOPE,REQUEST);
  const rejected=assert.rejects(pending,{code:'readonly-execution-timeout'});
  await state.started.promise;scheduler.fireAll();state.execDeferred.reject(new Error('container stopped'));
  await rejected;assert.equal(state.cleanupCalls,1);
});

test('SDK failed timeout receipt with exit zero is preserved as failed', async () => {
  const receipt={details:{status:'failed',exitCode:0,timedOut:true},content:[{type:'text',text:'Command timed out'}]};
  const {handle,state}=makeHandle();state.execImpl=async()=>receipt;
  const {runner,afterCalls}=makeRunner({prepare:async()=>handle});
  assert.equal(await runner.execute(SCOPE,REQUEST),receipt);
  assert.equal(afterCalls[0].result.details.status,'failed');
  assert.equal(afterCalls[0].result.details.timedOut,true);
  assert.equal(state.cleanupCalls,1);
});

test('success preserves exact SDK receipt and emits one after hook with full scope/internal id/params', async () => {
  const receipt = {details: {status: 'completed', exitCode: 0}, extra: 'sdk-receipt'};
  const {handle, state} = makeHandle();
  state.execDeferred = deferred();
  const {runner, authority, afterCalls} = makeRunner({prepare: async () => handle});

  const p = runner.execute(SCOPE, REQUEST);
  await state.started.promise;
  state.execDeferred.resolve(receipt);
  const result = await p;

  assert.equal(result, receipt, 'exact SDK receipt identity preserved');
  assert.equal(authority.calls.length, 1);
  const call = authority.calls[0];
  assert.equal(call.kind, 'exec');
  assert.match(call.internalId, /^ods-readonly-/);
  assert.equal(call.params, handle.params);
  assert.equal(afterCalls.length, 1);
  const after = afterCalls[0];
  assert.equal(after.toolName, 'exec');
  assert.equal(after.toolCallId, call.internalId);
  assert.equal(after.agentId, SCOPE.agentId);
  assert.equal(after.runId, SCOPE.runId);
  assert.equal(after.sessionId, SCOPE.sessionId);
  assert.equal(after.sessionKey, SCOPE.sessionKey);
  assert.equal(after.startArgs, handle.params);
  assert.equal(after.startedAt, 1000);
  assert.equal(after.result, receipt);
  assert.equal(after.error, undefined);
  assert.equal(state.cleanupCalls, 1);
});

test('pre-aborted signal rejects with no authority and no after hook', async () => {
  const {runner, authority, afterCalls, prepareCalls} = makeRunner();
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(runner.execute(SCOPE, REQUEST, controller.signal), /readonly-execution-cancelled/);
  assert.equal(authority.calls.length, 0);
  assert.equal(afterCalls.length, 0);
  assert.equal(prepareCalls.length, 0);
});

test('cancel during preparation waits for prepare, cleans up, and never executes', async () => {
  const prep = deferred();
  const {handle, state} = makeHandle();
  const {runner, authority, afterCalls} = makeRunner({prepare: () => prep.promise});

  const p = runner.execute(SCOPE, REQUEST);
  await Promise.resolve();
  const cancelP = runner.cancel(SCOPE);
  await Promise.resolve();
  prep.resolve(handle);
  await assert.rejects(p, /readonly-execution-cancelled/);
  const cancelled = await cancelP;

  assert.equal(cancelled, true);
  assert.equal(authority.calls.length, 0, 'no execution after cancel during prep');
  assert.equal(state.cleanupCalls, 1);
  assert.equal(afterCalls.length, 0, 'no after hook when never attempted');
});

test('cancel during execution waits for SDK settlement and cleanup before resolving', async () => {
  const {handle, state} = makeHandle();
  state.execDeferred = deferred();
  state.cleanupDeferred = deferred();
  const {runner, afterCalls} = makeRunner({prepare: async () => handle});

  const p = runner.execute(SCOPE, REQUEST);
  await state.started.promise;
  const cancelP = runner.cancel(SCOPE);
  await Promise.resolve();

  let cancelSettled = false;
  cancelP.then(() => { cancelSettled = true; });
  await Promise.resolve();
  assert.equal(cancelSettled, false, 'cancel must not resolve before SDK settles');

  state.execDeferred.resolve({details: {status: 'completed', exitCode: 0}});
  await state.started.promise;
  assert.equal(cancelSettled, false, 'cancel must not resolve before cleanup settles');

  state.cleanupDeferred.resolve();
  await assert.rejects(p, /readonly-execution-cancelled/);
  assert.equal(await cancelP, true);
  assert.equal(state.cleanupCalls, 1);
  assert.equal(afterCalls.length, 1, 'after hook still fires for attempted execution');
});

test('cancel after SDK settlement but during cleanup rejects the success result', async () => {
  const {handle, state} = makeHandle();
  state.cleanupDeferred = deferred();
  const {runner} = makeRunner({prepare: async () => handle});

  const p = runner.execute(SCOPE, REQUEST);
  await state.cleaning.promise;
  // SDK has settled; cleanup is pending.
  const cancelP = runner.cancel(SCOPE);
  await Promise.resolve();
  state.cleanupDeferred.resolve();
  await assert.rejects(p, /readonly-execution-cancelled/);
  assert.equal(await cancelP, true);
});

test('cleanup failure latches new work; cancel retries once and clears only after proof', async () => {
  const {handle, state} = makeHandle();
  let cleanupAttempts = 0;
  state.cleanupImpl = () => {
    cleanupAttempts++;
    if (cleanupAttempts === 1) return Promise.reject(new Error('cleanup-boom'));
    return Promise.resolve();
  };
  const {runner, authority} = makeRunner({prepare: async () => handle});

  await assert.rejects(runner.execute(SCOPE, REQUEST), /readonly-cleanup-unconfirmed/);
  assert.equal(cleanupAttempts, 1);

  // New work latched.
  await assert.rejects(runner.execute({...SCOPE, toolCallId: 'call-2'}, REQUEST), /readonly-recovery-required/);
  assert.equal(authority.calls.length, 1);

  // Cancel retries cleanup once and clears the latch.
  const cancelled = await runner.cancel(SCOPE);
  assert.equal(cancelled, true);
  assert.equal(cleanupAttempts, 2);

  // Now new work is allowed again.
  assert.equal(runner.pendingFor(SCOPE), 0);
  await runner.execute({...SCOPE, toolCallId: 'call-3'}, REQUEST);
  assert.equal(authority.calls.length, 2);
});

test('unrelated scope cannot abort an active job', async () => {
  const {handle, state} = makeHandle();
  state.execDeferred = deferred();
  const {runner} = makeRunner({prepare: async () => handle});

  const p = runner.execute(SCOPE, REQUEST);
  await state.started.promise;

  const other = {...SCOPE, runId: 'run-other'};
  const cancelled = await runner.cancel(other);
  assert.equal(cancelled, true); // No owned work means no unfinished cancellation.
  assert.equal(state.signal.aborted, false);
  assert.equal(runner.pendingFor(SCOPE), 1);

  state.execDeferred.resolve({details: {status: 'completed', exitCode: 0}});
  const result = await p;
  assert.equal(result.details.status, 'completed');
});

test('duplicate active exact tool call is rejected', async () => {
  const {handle, state} = makeHandle();
  state.execDeferred = deferred();
  const {runner} = makeRunner({prepare: async () => handle});

  const p = runner.execute(SCOPE, REQUEST);
  await state.started.promise;

  await assert.rejects(runner.execute(SCOPE, REQUEST), /readonly-execution-duplicate/);

  state.execDeferred.resolve({details: {status: 'completed', exitCode: 0}});
  await p;
});

test('terminal failed exit receipt is preserved and returned', async () => {
  const receipt = {details: {status: 'failed', exitCode: 2}};
  const {handle, state} = makeHandle();
  state.execDeferred = deferred();
  const {runner, afterCalls} = makeRunner({prepare: async () => handle});

  const p = runner.execute(SCOPE, REQUEST);
  await state.started.promise;
  state.execDeferred.resolve(receipt);
  const result = await p;

  assert.equal(result, receipt);
  assert.equal(afterCalls.length, 1);
  assert.equal(afterCalls[0].result, receipt);
  assert.equal(afterCalls[0].error, undefined);
});

test('unexpected running status rejects and cleans up', async () => {
  const {handle, state} = makeHandle();
  state.execDeferred = deferred();
  const {runner, afterCalls} = makeRunner({prepare: async () => handle});

  const p = runner.execute(SCOPE, REQUEST);
  await state.started.promise;
  state.execDeferred.resolve({details: {status: 'running', exitCode: null}});

  await assert.rejects(p, /readonly-execution-unsettled/);
  assert.equal(state.cleanupCalls, 1);
  assert.equal(afterCalls.length, 1);
  assert.match(afterCalls[0].error, /readonly-execution-unsettled/);
});

test('config drift before execution rejects and cleans up', async () => {
  const {handle, state} = makeHandle();
  state.stillCurrent = false;
  const {runner, authority, afterCalls} = makeRunner({prepare: async () => handle});

  await assert.rejects(runner.execute(SCOPE, REQUEST), /readonly-runtime-changed/);
  assert.equal(authority.calls.length, 0);
  assert.equal(state.cleanupCalls, 1);
  assert.equal(afterCalls.length, 0);
});

test('config drift after cleanup rejects the success result', async () => {
  const {handle, state} = makeHandle();
  state.cleanupDeferred = deferred();
  const {runner} = makeRunner({prepare: async () => handle});
  const pending = runner.execute(SCOPE, REQUEST);
  const rejected = assert.rejects(pending, /readonly-runtime-changed/);
  await state.cleaning.promise;
  state.stillCurrent = false;
  state.cleanupDeferred.resolve();
  await rejected;
});

test('SDK throw cleans up and reports failure', async () => {
  const {handle, state} = makeHandle();
  state.execImpl = () => Promise.reject(new Error('sdk-boom'));
  const {runner, afterCalls} = makeRunner({prepare: async () => handle});

  await assert.rejects(runner.execute(SCOPE, REQUEST), /sdk-boom/);
  assert.equal(state.cleanupCalls, 1);
  assert.equal(afterCalls.length, 1);
  assert.match(afterCalls[0].error, /sdk-boom/);
});

test('timeout fires via injected scheduler and reports readonly-execution-timeout', async () => {
  const scheduler = makeScheduler();
  const {handle, state} = makeHandle();
  state.execDeferred = deferred();
  const {runner, afterCalls} = makeRunner({scheduler, prepare: async () => handle});

  const p = runner.execute(SCOPE, REQUEST);
  await state.started.promise;

  assert.equal(scheduler.timers.length, 1);
  scheduler.fireAll();

  // SDK settles after timeout abort.
  state.execDeferred.resolve({details: {status: 'completed', exitCode: 0}});

  await assert.rejects(p, (err) => {
    assert.equal(err.code, 'readonly-execution-timeout');
    return true;
  });
  assert.equal(state.cleanupCalls, 1);
  assert.equal(afterCalls.length, 1);
  assert.match(afterCalls[0].error, /readonly-execution-timeout/);
});

test('concurrent cancel calls do not trigger more than one cleanup', async () => {
  const {handle, state} = makeHandle();
  state.execDeferred = deferred();
  state.cleanupDeferred = deferred();
  const {runner} = makeRunner({prepare: async () => handle});

  const p = runner.execute(SCOPE, REQUEST);
  await state.started.promise;

  const c1 = runner.cancel(SCOPE);
  const c2 = runner.cancel(SCOPE);
  await Promise.resolve();

  state.execDeferred.resolve({details: {status: 'completed', exitCode: 0}});
  await state.started.promise;
  state.cleanupDeferred.resolve();

  await assert.rejects(p, /readonly-execution-cancelled/);
  await Promise.all([c1, c2]);
  assert.equal(state.cleanupCalls, 1, 'cleanup must be invoked exactly once');
});

const makeRunnerWithSettled = (opts = {}) => {
  const scheduler = opts.scheduler ?? makeScheduler();
  const authority = opts.authority ?? makeAuthority();
  const afterCalls = [];
  const settledCalls = [];
  const prepareImpl = opts.prepare ?? (async () => makeHandle().handle);
  const runner = createReadonlyExecutionRunner({
    prepare: async (scope, request) => prepareImpl(scope, request),
    authority,
    afterToolCall: async (payload) => { afterCalls.push(payload); },
    onSettled: async (scope) => { settledCalls.push(scope); if (opts.onSettled) return opts.onSettled(scope); },
    clock: () => 1000,
    setTimer: scheduler.setTimer,
    clearTimer: scheduler.clearTimer
  });
  return {runner, scheduler, authority, afterCalls, settledCalls};
};

test('onSettled waits for execution and cleanup before resolving', async () => {
  const {handle, state} = makeHandle();
  state.execDeferred = deferred();
  state.cleanupDeferred = deferred();
  const {runner, settledCalls} = makeRunnerWithSettled({prepare: async () => handle});
  const p = runner.execute(SCOPE, REQUEST);
  await state.started.promise;
  state.execDeferred.resolve({details: {status: 'completed', exitCode: 0}});
  await state.cleaning.promise;
  assert.equal(settledCalls.length, 0, 'settlement must wait for cleanup');
  state.cleanupDeferred.resolve();
  await p;
  assert.equal(settledCalls.length, 1);
  assert.equal(settledCalls[0].runId, SCOPE.runId);
  assert.equal(settledCalls[0].toolCallId, SCOPE.toolCallId);
});

test('onSettled fires exactly once per successful execution', async () => {
  const {handle} = makeHandle();
  const {runner, settledCalls} = makeRunnerWithSettled({prepare: async () => handle});
  await runner.execute(SCOPE, REQUEST);
  assert.equal(settledCalls.length, 1);
  await runner.cancel(SCOPE);
  assert.equal(settledCalls.length, 1, 'cancel after settlement must not re-settle');
});

test('Stop cannot settle an admission while SDK execution is still pending', async () => {
  const {handle, state} = makeHandle();
  state.execDeferred = deferred();
  const {runner, settledCalls} = makeRunnerWithSettled({prepare: async () => handle});
  const pending = runner.execute(SCOPE, REQUEST);
  const rejected = assert.rejects(pending, /readonly-execution-cancelled/);
  await state.started.promise;
  const cancelling = runner.cancel(SCOPE);
  await state.cleaning.promise;
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(settledCalls.length, 0, 'cleanup alone cannot prove SDK execution settled');
  assert.equal(runner.pendingFor(SCOPE), 1);
  state.execDeferred.resolve({details:{status:'completed',exitCode:0}});
  await rejected;
  assert.equal(await cancelling, true);
  assert.equal(settledCalls.length, 1);
});

test('cleanup retry calls settlement after the retry succeeds', async () => {
  const {handle, state} = makeHandle();
  let attempts = 0;
  state.cleanupImpl = () => { attempts++; return attempts === 1 ? Promise.reject(new Error('cleanup-boom')) : Promise.resolve(); };
  const {runner, settledCalls} = makeRunnerWithSettled({prepare: async () => handle});
  await assert.rejects(runner.execute(SCOPE, REQUEST), /readonly-cleanup-unconfirmed/);
  assert.equal(settledCalls.length, 0, 'no settlement before cleanup is proven');
  assert.equal(await runner.cancel(SCOPE), true);
  assert.equal(attempts, 2);
  assert.equal(settledCalls.length, 1);
});

test('onSettled failure latches recovery and cancel retries settlement', async () => {
  const {handle} = makeHandle();
  let settleAttempts = 0;
  const {runner, settledCalls} = makeRunnerWithSettled({prepare: async () => handle,
    onSettled: async () => { if (++settleAttempts === 1) throw new Error('settle-boom'); }});
  await assert.rejects(runner.execute(SCOPE, REQUEST), /readonly-settlement-unconfirmed/);
  assert.equal(settledCalls.length, 1);
  await assert.rejects(runner.execute({...SCOPE, toolCallId: 'call-2'}, REQUEST), /readonly-recovery-required/);
  assert.equal(await runner.cancel(SCOPE), true);
  assert.equal(settleAttempts, 2);
  assert.equal(runner.pendingFor(SCOPE), 0);
  await runner.execute({...SCOPE, toolCallId: 'call-3'}, REQUEST);
  assert.equal(settleAttempts, 3);
});

test('pre-aborted signal performs no execution and settles safely', async () => {
  const {runner, authority, afterCalls, settledCalls} = makeRunnerWithSettled();
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(runner.execute(SCOPE, REQUEST, controller.signal), /readonly-execution-cancelled/);
  assert.equal(authority.calls.length, 0);
  assert.equal(afterCalls.length, 0);
  assert.equal(settledCalls.length, 1, 'preparation never started, so absence is authoritative');
  assert.equal(runner.pendingFor(SCOPE), 0);
});
