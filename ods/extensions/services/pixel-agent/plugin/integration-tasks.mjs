import { submitIntegration } from './integrations.mjs';

// Managed job identity is held by the owner service; prose is never custody.
const SYNC_MS = 60000;
const str = value => typeof value === 'string' ? value : '';
const equal = (left, right) => JSON.stringify(left) === JSON.stringify(right);
function digestJob(plan, agentId) {
  return {name: plan.name, description: plan.description, enabled: true, agentId,
    schedule: {kind: 'cron', expr: plan.expr, tz: plan.tz}, sessionTarget: 'isolated', wakeMode: 'now',
    payload: {kind: 'agentTurn', message: plan.message, timeoutSeconds: plan.timeoutSeconds,
      lightContext: true, toolsAllow: plan.tools}, delivery: {mode: 'none'}, deleteAfterRun: false};
}
function sameSchedule(job, wanted) {
  return job.name === wanted.name && job.description === wanted.description
    && job.agentId === wanted.agentId && job.enabled === true
    && job.schedule?.kind === 'cron' && job.schedule.expr === wanted.schedule.expr
    && (job.schedule.tz ?? 'UTC') === wanted.schedule.tz
    && job.sessionTarget === 'isolated' && job.wakeMode === 'now'
    && job.payload?.kind === 'agentTurn' && job.payload.message === wanted.payload.message
    && job.payload.timeoutSeconds === wanted.payload.timeoutSeconds && job.payload.lightContext === true
    && equal(job.payload.toolsAllow ?? [], wanted.payload.toolsAllow)
    && equal(job.delivery, wanted.delivery) && job.deleteAfterRun === false;
}
function management(plan) {
  const value = plan?.management;
  if (!value || !/^[a-f0-9]{32}$/.test(value.nonce) ||
      !(value.jobId === null || typeof value.jobId === 'string' && value.jobId.length > 0 && value.jobId.length <= 200)) {
    throw new Error('The digest job identity could not be verified.');
  }
  return {...value};
}
export function createTaskHooks({submit = submitIntegration, agentId = 'pixel', warn = () => {}, timers = globalThis} = {}) {
  let cron, timer, generation = 0, active = false, syncing;
  const remember = context => { const service = context?.getCron?.(); if (service) cron = service; };
  const current = epoch => { if (!active || epoch !== generation) throw new Error('Digest scheduling stopped.'); };
  async function control(action, identity, extra = {}) {
    const reply = await submit({action, nonce: identity.nonce, jobId: identity.jobId, ...extra});
    if (!reply?.ok) throw new Error(reply?.message || 'The digest job identity could not be saved.');
    return reply;
  }
  async function syncOnce(epoch, service) {
    current(epoch);
    const reply = await submit({action: 'digest-plan'});
    current(epoch);
    if (!reply?.ok) throw new Error(reply?.message || 'The digest settings could not be read.');
    const {plan} = reply;
    if (!plan?.management && plan?.enabled === false) return 'off';
    const identity = management(plan), wanted = digestJob(plan, agentId);
    const jobs = await service.list({includeDisabled: true});
    current(epoch);
    let owned;
    if (identity.jobId !== null) {
      owned = jobs.find(job => job.id === identity.jobId);
      if (!owned) { await control('digest-job-clear', identity); return 'cleared'; }
      if (owned.agentId !== agentId || owned.description !== plan.description) {
        throw new Error('The recorded digest job was changed. It was not replaced or removed.');
      }
    } else {
      // Recover only our durable nonce-tagged creation after cron.add, never a legacy marker.
      const candidates = jobs.filter(job => job.agentId === agentId && job.description === plan.description);
      if (candidates.length > 1) throw new Error('More than one job matches the digest creation journal; none was changed.');
      if (candidates.length) {
        owned = candidates[0];
        if (owned.sessionTarget !== 'isolated' || owned.payload?.kind !== 'agentTurn' || owned.payload.lightContext !== true) {
          throw new Error('The pending digest job has unexpected execution settings; it was not adopted.');
        }
        await control('digest-job-record', identity, {jobId: owned.id});
        current(epoch);
        identity.jobId = owned.id;
      }
    }
    if (!plan.enabled) {
      if (!owned) return 'off';
      current(epoch);
      await service.remove(owned.id);
      // Finish bookkeeping for a mutation already submitted; stop waits for it.
      await control('digest-job-clear', identity);
      return 'removed';
    }
    if (!owned) {
      current(epoch);
      const added = await service.add(wanted);
      if (!added || typeof added.id !== 'string' || !added.id) throw new Error('OpenClaw did not return the digest job ID.');
      await control('digest-job-record', identity, {jobId: added.id});
      return 'added';
    }
    if (sameSchedule(owned, wanted)) return 'kept';
    current(epoch);
    await service.update(owned.id, wanted);
    return 'updated';
  }
  function syncDigest() {
    if (!cron) return Promise.resolve('no-cron');
    if (!active) return Promise.resolve('stopped');
    const epoch = generation, service = cron;
    if (syncing?.epoch === epoch) return syncing.promise;
    const previous = syncing?.promise;
    const record = {epoch, promise: undefined};
    record.promise = Promise.resolve(previous).catch(() => {}).then(() => syncOnce(epoch, service))
      .finally(() => { if (syncing === record) syncing = undefined; });
    syncing = record;
    return record.promise;
  }
  const syncQuietly = () => syncDigest().catch(error => warn(`Portal integrations: daily digest not scheduled: ${error.message}`));
  return {
    syncDigest,
    gatewayStart(_event, context) {
      remember(context);
      if (!active) { active = true; generation++; }
      if (!timer) { timer = timers.setInterval(syncQuietly, SYNC_MS); timer.unref?.(); }
      return syncQuietly();
    },
    async gatewayStop() {
      active = false; generation++;
      if (timer) timers.clearInterval(timer);
      timer = undefined;
      await syncing?.promise.catch(() => {});
    },
    async cronChanged(event, context) {
      remember(context);
      const owner = event?.agentId ?? event?.job?.agentId;
      if (event?.action !== 'finished' || owner !== agentId) return;
      const job = event.job ?? {};
      try {
        const reply = await submit({action: 'task-finished', jobId: str(event.jobId), name: str(job.name), description: str(job.description),
          status: str(event.status), summary: str(event.summary).slice(0, 4000), error: str(event.error).slice(0, 2000),
          runAtMs: Number.isFinite(event.runAtMs) ? event.runAtMs : 0,
          sessionId: str(event.sessionId), sessionKey: str(event.sessionKey), runId: str(event.runId)});
        if (!reply?.ok) warn(`Portal integrations: scheduled task notice failed: ${reply?.message}`);
      } catch (error) { warn(`Portal integrations: scheduled task notice failed: ${error.message}`); }
    },
  };
}
