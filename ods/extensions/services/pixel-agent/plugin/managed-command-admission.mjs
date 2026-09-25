import {randomUUID} from 'node:crypto';
import {performance} from 'node:perf_hooks';
import {setTimeout as delay} from 'node:timers/promises';

// One instance per gateway generation, sharing the actual synchronous access
// owner. Register with timeoutMs >= cleanupTimeoutMs + 5000. This module neither
// registers hooks nor activates policy. Its status is additive to owner status:
// unknown cleanup must block replacement/rollback even if the owner lost a slot.
// The runtime supplies controls only after its runner settles. cancel() cannot
// claim immediate Stop of a running root before those controls become available.
export function createManagedCommandAdmission({accessRuntime, cleanupTimeoutMs = 1000,
  pollIntervalMs = 10} = {}) {
  if (!['status', 'admit', 'finish'].every(key => typeof accessRuntime?.[key] === 'function') ||
      !Number.isSafeInteger(cleanupTimeoutMs) || cleanupTimeoutMs < 1 || cleanupTimeoutMs > 60000 ||
      !Number.isSafeInteger(pollIntervalMs) || pollIntervalMs < 1 || pollIntervalMs > cleanupTimeoutMs) {
    throw new Error('Invalid command admission owner or timing');
  }
  const records = new Map();
  let closed = false, unknown = false, shutdownPromise;
  const block = () => ({action: 'block', reason: 'ods-command-admission-unavailable'});
  const failure = () => new Error('Command activity cleanup is unproven');
  function fail(record) {
    closed = true; unknown = true;
    if (!record || record.done || record.error) return;
    record.error = failure();
    record.wake.abort();
    record.reject(record.error);
  }
  function makeRecord(commandId) {
    const record = {commandId, runId: `ods-command-${randomUUID()}`,
      wake: new AbortController(), started: false, done: false, error: null,
      cancelRequested: false, cancelPending: false};
    record.promise = new Promise((resolve, reject) => {
      record.resolve = resolve; record.reject = reject;
    });
    // Admission or shutdown may fail before the runtime has received finish.
    // Observe rejection without replacing the original sticky promise.
    void record.promise.catch(() => {});
    return record;
  }
  function inspect(record) {
    const value = record.inspectProcess();
    if (value?.state === 'not-started') return true;
    if (!['running', 'exited'].includes(value?.state) || value.scope !== 'process-group' ||
        !Number.isSafeInteger(value.pid) || value.pid <= 0) throw failure();
    return value.state === 'exited';
  }
  async function drain(record) {
    try {
      while (!record.error) {
        const remaining = record.deadline - performance.now();
        if (remaining <= 0) throw failure();
        const terminal = inspect(record);
        if (terminal && !record.cancelPending) {
          // No await between the last proof and releasing this private owner ID.
          const result = accessRuntime.finish(undefined, {runId: record.runId});
          if (result !== undefined) {
            void Promise.resolve(result).catch(() => {});
            throw failure(); // This composition requires the synchronous owner.
          }
          record.done = true;
          records.delete(record.commandId);
          record.resolve();
          return;
        }
        await delay(Math.min(pollIntervalMs, remaining), undefined, {signal: record.wake.signal});
      }
    } catch { fail(record); }
  }
  function finish(record, event) {
    if (record.started || record.error || record.done) return record.promise;
    record.started = true;
    try {
      if (event?.commandId !== record.commandId || typeof event.inspectProcess !== 'function' ||
          typeof event.cancelProcess !== 'function') throw failure();
      record.inspectProcess = event.inspectProcess;
      record.cancelProcess = event.cancelProcess;
      record.deadline = performance.now() + cleanupTimeoutMs;
      // Store controls synchronously so an explicit owner cancel can arrive
      // before the first inspection, without creating a second release path.
      queueMicrotask(() => { void drain(record); });
    } catch { fail(record); }
    return record.promise;
  }
  function beforeCommandRun(event, context) {
    if (closed) return block();
    let commandId;
    try {
      commandId = event?.commandId;
      if (typeof commandId !== 'string' || !commandId || commandId.length > 256 ||
          records.has(commandId) || context?.signal?.aborted) return block();
      const owner = accessRuntime.status();
      if (owner?.available !== true || !['idle', 'busy'].includes(owner.phase)) return block();
    } catch { fail(); return block(); }
    const record = makeRecord(commandId);
    records.set(commandId, record);
    try {
      const decision = accessRuntime.admit(undefined, {runId: record.runId});
      if (decision?.outcome !== 'pass') {
        // The synchronous preflight already excluded held/interrupted state.
        // An owner can add its slot and then return block after a write fails;
        // even an unchanged available flag cannot prove no reservation exists.
        throw failure();
      }
    } catch { fail(record); return block(); }
    return {action: 'allow', finish: event => finish(record, event)};
  }
  function cancel(commandId) {
    const record = records.get(commandId);
    if (!record || !record.started || typeof record.cancelProcess !== 'function') {
      return Promise.reject(new Error('Command cancellation control is unavailable'));
    }
    if (!record.cancelRequested && !record.error) {
      record.cancelRequested = true; record.cancelPending = true;
      // Catch synchronous throws and late rejections. A hanging cancellation
      // cannot outlive the shared finish deadline or independently release work.
      void Promise.resolve().then(() => {
        if (!record.error && !record.done) return record.cancelProcess();
      }).then(() => { record.cancelPending = false; }, () => { fail(record); });
    }
    return record.promise;
  }
  function shutdown() {
    if (shutdownPromise) return shutdownPromise;
    closed = true;
    shutdownPromise = new Promise((resolve, reject) => {
      const rejectUnknown = () => {
        for (const record of records.values()) fail(record);
        unknown = true;
        reject(failure());
      };
      const timer = setTimeout(rejectUnknown, cleanupTimeoutMs);
      Promise.all([...records.values()].map(record => record.promise)).then(() => {
        clearTimeout(timer);
        if (unknown) rejectUnknown(); else resolve();
      }, () => { clearTimeout(timer); rejectUnknown(); });
    });
    void shutdownPromise.catch(() => {});
    return shutdownPromise;
  }
  return {beforeCommandRun, cancel, shutdown,
    status: () => ({closed, active: records.size, unknown})};
}
