import {isDeepStrictEqual} from 'node:util';
import {randomUUID} from 'node:crypto';
import {setTimeout as delay} from 'node:timers/promises';
import {normalizeProjectBuild} from './project-build.mjs';

const NAME = 'pixel_ods_project_build';
const terminal = value => ['succeeded', 'failed', 'cancelled'].includes(value?.status);
const key = scope => JSON.stringify([scope.sessionKey, scope.sessionId, scope.runId]);

// Shared by gateway-route and runtime registration passes. Factory contexts do
// not carry a live run ID; only admitted hook contexts establish that binding.
export function createProjectRunControl({wait = ms => delay(ms), now = () => performance.now()} = {}) {
  const pending = new Map(), runs = new Map();
  function before(event, context, decision) {
    if (decision?.block || context?.agentId !== 'pixel') return;
    const params = decision?.params ?? event?.params;
    const deferred = event?.toolName === 'tool_call' &&
      [NAME, `openclaw:pixel-ods:${NAME}`].includes(params?.id);
    if (event?.toolName !== NAME && !deferred) return;
    const id = context.toolCallId ?? event.toolCallId;
    if (!id || !context.runId || !context.sessionId || !context.sessionKey || pending.size >= 128) return;
    try { pending.set(id, {scope: {...context}, request: normalizeProjectBuild(deferred ? params.args : params), deferred}); }
    catch { /* Invalid requests never reach the controller. */ }
  }
  function bind(id, params, factory, request) {
    let entry = pending.get(id);
    if (!entry) {
      const matches = [...pending].filter(([parentId, item]) => {
        const parent = parentId.trim().replace(/[^A-Za-z0-9_.:-]+/g, '_').slice(0, 120) || 'call';
        const prefix = `tool_search_code:${parent}:${NAME}:`;
        return item.deferred && id.startsWith(prefix) && /^[1-9][0-9]*$/.test(id.slice(prefix.length));
      });
      if (matches.length === 1) entry = matches[0][1];
    }
    if (!entry || entry.used || !isDeepStrictEqual(entry.request, normalizeProjectBuild(params)) ||
        ['agentId', 'sessionId', 'sessionKey'].some(name => entry.scope[name] !== factory?.[name])) {
      throw Error('project call is not bound to an admitted run');
    }
    entry.used = true;
    // The probe reads cached image metadata. It cannot create or adopt a job,
    // and a lost metadata response must not make Stop report unknown execution.
    if (entry.request.action === 'capabilities') return (normalized, options) => {
      if (!isDeepStrictEqual(normalized, entry.request)) throw Error('capability query cannot execute project work');
      return request(normalized, options);
    };
    const identity = key(entry.scope);
    let run = runs.get(identity);
    if (!run) {
      if (runs.size >= 128) {
        for (const [oldKey, old] of runs) {
          if (!old.unknown && old.pending === 0 && [...old.jobs.values()].every(job => terminal(job.receipt))) {
            runs.delete(oldKey);
            if (runs.size < 128) break;
          }
        }
      }
      if (runs.size >= 128) throw Error('project run tracking is full');
      run = {jobs: new Map(), pending: 0, unknown: false, stopping: false};
      runs.set(identity, run);
    }
    if (run.stopping) throw Error('project run is stopping');
    return async (normalized, options) => {
      const createsJob=['submit','diagnose'].includes(normalized.action);
      if (run.stopping && createsJob) throw Error('project run is stopping');
      run.pending++;
      try {
        const receipt = await request(normalized, options);
        if (receipt?.schemaVersion === 1 && receipt.kind === 'ods-project-job' &&
            /^ods-project-[a-f0-9]{24}$/.test(receipt.jobId ?? '') &&
            (normalized.action === 'submit' ? receipt.project === normalized.project
              : normalized.action==='diagnose' ? receipt.purpose==='diagnostic' && receipt.project===null
                && receipt.scope==='managed-executor' && receipt.runtime===normalized.runtime : receipt.jobId === normalized.jobId)) {
          // Merely observing a previous run's job does not transfer ownership
          // to this run's Stop button.
          if (createsJob || run.jobs.has(receipt.jobId)) {
            run.jobs.set(receipt.jobId, {receipt, request});
          }
        } else if (createsJob && !['denied', 'invalid-request'].includes(receipt?.status)
            && !(normalized.action==='diagnose' && receipt?.kind==='ods-project-diagnostic'
              && receipt.scope==='managed-executor' && receipt.runtime===normalized.runtime
              && receipt.code==='unavailable' && receipt.cleanup==='not-started')) {
          run.unknown = true;
        }
        return receipt;
      } catch (error) {
        if (createsJob) run.unknown = true;
        throw error;
      } finally { run.pending--; }
    };
  }
  async function cancel(scope) {
    const run = runs.get(key(scope));
    if (!run) return true;
    run.stopping = true;
    const deadline = now() + 10000;
    let confirmed = !run.unknown && run.pending === 0;
    await Promise.all([...run.jobs].map(async ([jobId, job]) => {
      if (terminal(job.receipt)) return;
      const signal = AbortSignal.timeout(10000);
      try {
        let receipt = await job.request({schemaVersion: 1, action: 'cancel', jobId},
          {toolCallId: `ods-stop-${randomUUID()}`, signal});
        while (receipt?.jobId === jobId && ['queued', 'running'].includes(receipt.status) && now() < deadline) {
          await wait(100);
          receipt = await job.request({schemaVersion: 1, action: 'observe', jobId},
            {toolCallId: `ods-stop-${randomUUID()}`, signal});
        }
        if (receipt?.schemaVersion !== 1 || receipt.kind !== 'ods-project-job' || receipt.jobId !== jobId || !terminal(receipt)) confirmed = false;
        else job.receipt = receipt;
      } catch { confirmed = false; }
    }));
    return confirmed;
  }
  return {before, bind, cancel, after: (event, context) => pending.delete(context?.toolCallId ?? event?.toolCallId)};
}
