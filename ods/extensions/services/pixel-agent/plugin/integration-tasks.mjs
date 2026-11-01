import { submitIntegration } from './integrations.mjs';

// Gateway hooks for scheduled tasks, which are OpenClaw's own cron jobs. A finished run of one
// of Portal's tasks goes to the host integrations service, which notifies the owner as they
// chose, and the daily news digest is kept as one more cron job that follows their settings.
const SYNC_MS = 60000;

const str = value => (typeof value === 'string' ? value : '');

function digestJob(plan, agentId) {
  return {name: plan.name, description: plan.description, enabled: true, agentId,
    schedule: {kind: 'cron', expr: plan.expr, tz: plan.tz}, sessionTarget: 'isolated', wakeMode: 'now',
    payload: {kind: 'agentTurn', message: plan.message, timeoutSeconds: plan.timeoutSeconds, lightContext: true, toolsAllow: plan.tools},
    delivery: {mode: 'none'}, deleteAfterRun: false};
}

const sameSchedule = (job, plan) => job.schedule?.kind === 'cron' && job.schedule.expr === plan.expr
  && (job.schedule.tz ?? 'UTC') === plan.tz && job.payload?.message === plan.message
  && job.payload?.timeoutSeconds === plan.timeoutSeconds
  && JSON.stringify(job.payload?.toolsAllow ?? []) === JSON.stringify(plan.tools) && job.enabled !== false;

// Hooks for OpenClaw's cron: finished runs of Portal's tasks go to the integrations service,
// and the digest job follows the owner's digest settings.
export function createTaskHooks({submit = submitIntegration, agentId = 'pixel', warn = () => {}, timers = globalThis} = {}) {
  let cron, timer;
  const remember = context => {
    const service = context?.getCron?.();
    if (service) cron = service;
  };

  // One sync at a time: two that overlap could both find no digest job and both add one.
  let syncing;
  function syncDigest() {
    syncing ??= syncOnce().finally(() => { syncing = undefined; });
    return syncing;
  }

  async function syncOnce() {
    if (!cron) return 'no-cron';
    const reply = await submit({action: 'digest-plan'});
    if (!reply?.ok) throw new Error(reply?.message || 'The digest settings could not be read.');
    const {plan} = reply;
    const mine = (await cron.list({includeDisabled: true})).filter(job => job.description === plan.description);
    if (!plan.enabled) {
      for (const job of mine) await cron.remove(job.id);
      return mine.length ? 'removed' : 'off';
    }
    for (const job of mine.slice(1)) await cron.remove(job.id);
    if (!mine.length) {
      await cron.add(digestJob(plan, agentId));
      return 'added';
    }
    if (sameSchedule(mine[0], plan)) return 'kept';
    const {name, description, ...patch} = digestJob(plan, agentId);
    await cron.update(mine[0].id, patch);
    return 'updated';
  }

  const syncQuietly = () => syncDigest().catch(error => warn(`Portal integrations: daily digest not scheduled: ${error.message}`));

  return {
    syncDigest,
    gatewayStart(_event, context) {
      remember(context);
      if (!timer) {
        timer = timers.setInterval(syncQuietly, SYNC_MS);
        timer.unref?.();
      }
      return syncQuietly();
    },
    gatewayStop() {
      if (timer) timers.clearInterval(timer);
      timer = undefined;
    },
    async cronChanged(event, context) {
      remember(context);
      if (event?.action !== 'finished' || (event.agentId && event.agentId !== agentId)) return;
      const job = event.job ?? {};
      try {
        const reply = await submit({action: 'task-finished', jobId: str(event.jobId), name: str(job.name), description: str(job.description),
          status: str(event.status), summary: str(event.summary).slice(0, 4000), error: str(event.error).slice(0, 2000),
          runAtMs: Number.isFinite(event.runAtMs) ? event.runAtMs : 0});
        if (!reply?.ok) warn(`Portal integrations: scheduled task notice failed: ${reply?.message}`);
      } catch (error) {
        warn(`Portal integrations: scheduled task notice failed: ${error.message}`);
      }
    },
  };
}
