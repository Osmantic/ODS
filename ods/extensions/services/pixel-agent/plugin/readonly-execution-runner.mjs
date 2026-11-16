import {randomUUID} from 'node:crypto';

const fail = (code, cause) => Object.assign(new Error(code), {code, ...(cause ? {cause} : {})});
const required = ['runId','sessionId','sessionKey'];
const validScope = scope => scope && required.every(key => typeof scope[key] === 'string' && scope[key]);
const matches = (job, scope) => validScope(scope) && required.every(key => job.scope[key] === scope[key]) &&
  (scope.agentId === undefined || scope.agentId === 'pixel');

// The provider owns sandbox provisioning and rollback. This layer never
// abandons an SDK promise: Stop is acknowledged only after execution settles
// and the owned sandbox/registry cleanup is proven.
// A preparation error must carry readonlyCleanupConfirmed:true after verified
// rollback, or readonlyRecovery:{cleanup} for a partial allocation. Unqualified
// preparation failures retain the recovery latch rather than imply absence.
export function createReadonlyExecutionRunner({prepare, authority, afterToolCall, clock = Date.now,
  setTimer = setTimeout, clearTimer = clearTimeout}) {
  if (typeof prepare !== 'function' || typeof authority?.run !== 'function' || typeof afterToolCall !== 'function')
    throw new TypeError('readonly runner dependencies required');
  const jobs = new Set();
  function cleanup(job, retry = false) {
    if (job.cleaned) return Promise.resolve();
    if (!job.handle) return Promise.reject(fail('readonly-cleanup-unconfirmed'));
    if (retry && job.cleanupError) {
      job.cleanupPromise = undefined;
      job.cleanupError = undefined;
    }
    if (!job.cleanupPromise) {
      job.cleanupPromise = Promise.resolve().then(() => job.handle.cleanup()).then(() => {
        job.cleaned = true; job.cleanupError = undefined;
      }, error => { job.cleanupError = error; throw error; });
      job.cleanupPromise.catch(() => {}); // An abort starts cleanup; the owner still awaits it.
    }
    return job.cleanupPromise;
  }
  async function execute(suppliedScope, request, signal) {
    if (!validScope(suppliedScope) || suppliedScope.agentId !== 'pixel' ||
        typeof suppliedScope.toolCallId !== 'string' || !suppliedScope.toolCallId)
      throw fail('readonly-scope-invalid');
    if (!Number.isInteger(request?.timeoutSeconds) || request.timeoutSeconds < 1 || request.timeoutSeconds > 60)
      throw fail('readonly-request-invalid');
    if ([...jobs].some(job => job.finished && !job.cleaned)) throw fail('readonly-recovery-required');
    const scope = Object.freeze({...suppliedScope});
    if ([...jobs].some(job => matches(job,scope) && job.scope.toolCallId === scope.toolCallId))
      throw fail('readonly-execution-duplicate');
    if (signal?.aborted) throw fail('readonly-execution-cancelled');
    const controller = new AbortController();
    const job = {scope, controller, handle:null, finished:false, cleaned:false};
    job.done = new Promise(resolve => { job.resolveDone = resolve; });
    jobs.add(job);
    const forwardAbort = () => controller.abort(signal?.reason);
    const onAbort = () => { if (job.handle) void cleanup(job).catch(() => {}); };
    signal?.addEventListener('abort', forwardAbort, {once:true});
    controller.signal.addEventListener('abort', onAbort, {once:true});
    if (signal?.aborted) forwardAbort();
    const abortFailure = () => fail(controller.signal.reason?.code === 'readonly-execution-timeout'
      ? 'readonly-execution-timeout' : 'readonly-execution-cancelled');
    let timer, result, failure, attempted = false, preparationStarted = false, startedAt, internalId;
    try {
      if (controller.signal.aborted) throw abortFailure();
      preparationStarted = true;
      job.handle = await prepare(scope,request);
      if (!job.handle || typeof job.handle.cleanup !== 'function' ||
          typeof job.handle.stillCurrent !== 'function' || typeof job.handle.exec?.execute !== 'function')
        throw fail('readonly-preparation-invalid');
      if (controller.signal.aborted) throw abortFailure();
      if (job.handle.stillCurrent() !== true) throw fail('readonly-runtime-changed');
      internalId = 'ods-readonly-' + randomUUID();
      startedAt = clock();
      timer = setTimer(() => controller.abort(fail('readonly-execution-timeout')),
        request.timeoutSeconds * 1000 + 2000);
      timer.unref?.();
      attempted = true;
      result = await authority.run(scope,'exec',internalId,job.handle.params,
        () => job.handle.exec.execute(internalId,job.handle.params,controller.signal));
      if (controller.signal.aborted) throw abortFailure();
      if (job.handle.stillCurrent() !== true) throw fail('readonly-runtime-changed');
      if (!['completed','failed'].includes(result?.details?.status) || !Number.isInteger(result.details.exitCode))
        throw fail('readonly-execution-unsettled');
    } catch (error) {
      if (!job.handle && typeof error?.readonlyRecovery?.cleanup === 'function')
        job.handle = error.readonlyRecovery;
      failure = controller.signal.aborted ? abortFailure() : error;
      if (error?.readonlyCleanupConfirmed === true) job.rollbackConfirmed = true;
    } finally {
      clearTimer(timer);
      if (job.handle) {
        try { await cleanup(job); }
        catch (error) { failure = fail('readonly-cleanup-unconfirmed',error); }
      } else {
        job.cleaned = !preparationStarted || job.rollbackConfirmed === true;
        if (!job.cleaned) failure = fail('readonly-cleanup-unconfirmed',failure);
      }
      if (!failure && controller.signal.aborted) failure = abortFailure();
      if (!failure) {
        try { if (job.handle.stillCurrent() !== true) failure = fail('readonly-runtime-changed'); }
        catch (error) { failure = error; }
      }
      if (attempted) {
        try {
          await afterToolCall({toolName:'exec',toolCallId:internalId,agentId:scope.agentId,
            runId:scope.runId,sessionId:scope.sessionId,sessionKey:scope.sessionKey,
            startArgs:job.handle.params,startedAt,
            ...(failure ? {error:String(failure.code ?? failure.message ?? 'readonly-execution-failed')} : {result})});
        } catch (error) { failure ??= error; }
      }
      signal?.removeEventListener('abort',forwardAbort);
      controller.signal.removeEventListener('abort',onAbort);
      job.finished = true;
      if (job.cleaned) jobs.delete(job);
      job.resolveDone();
    }
    if (failure) throw failure;
    return result;
  }
  async function cancel(scope) {
    if (!validScope(scope)) return false;
    const selected = [...jobs].filter(job => matches(job,scope));
    for (const job of selected) job.controller.abort(fail('readonly-execution-cancelled'));
    await Promise.all(selected.map(job => job.done));
    for (const job of selected) {
      if (!job.cleaned && job.handle) {
        try { await cleanup(job,true); } catch { /* Preserve the failed job and recovery latch. */ }
      }
      if (job.cleaned) jobs.delete(job);
    }
    return selected.every(job => job.finished && job.cleaned);
  }
  return {execute,cancel,pendingFor:scope => [...jobs].filter(job => matches(job,scope)).length};
}
