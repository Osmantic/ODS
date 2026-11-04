import { submitIntegration } from './integrations.mjs';

// Gateway hooks for scheduled tasks, which are OpenClaw's own cron jobs. A finished run of one
// of Portal's tasks goes to the host integrations service, which notifies the owner as they
// chose, and the daily news digest is kept as one more cron job that follows their settings.
// A reminder is a one-off cron job too: the service sends it when the job starts, so it arrives
// on time however long the job's own agent turn takes.
const SYNC_MS = 60000;
export const REMINDER_MARK = 'Portal reminder: ';
const isReminder = job => str(job?.description).startsWith(REMINDER_MARK);
// OpenClaw may load this plugin more than once in the gateway process: the instance whose
// hooks see gateway start is not always the one whose tools run. They share the scheduler here.
const SHARED_CRON = Symbol.for('ods.pixel-ods.getCron');

const str = value => (typeof value === 'string' ? value : '');

function digestJob(plan, agentId) {
  return {name: plan.name, description: plan.description, enabled: true, agentId,
    schedule: {kind: 'cron', expr: plan.expr, tz: plan.tz}, sessionTarget: 'isolated', wakeMode: 'now',
    payload: {kind: 'agentTurn', message: plan.message, timeoutSeconds: plan.timeoutSeconds, lightContext: true, toolsAllow: plan.tools},
    delivery: {mode: 'none'}, deleteAfterRun: false};
}

// A turn that asks for a reminder gets a note naming the tool: pixel_ods_notify sits behind
// tool search, and without it a small model says it cannot schedule anything (#7794).
const ASKS_REMINDER = /\bremind(?:er|ers|ing)?\b|\blembr(?:e|ete|ar|ança|ete-me)\b/i;
// The exact call, because a small model that only knows the tool's name searches and describes it
// again and again instead of calling it (e2e: three attempts for "Remind me in 3 minutes ...").
// The example uses the owner's own delay when the message gives one: a fixed example value is
// copied as is (e2e: "in 3 minutes" was scheduled for 10, the example's number).
const DELAY = /\b(?:in|daqui a|em)\s+(\d{1,4})\s*(min(?:ute|uto)?s?|h(?:ours?|oras?)?)\b/i;
const REMINDER_CALL = 'To remind the owner later, make one call: tool_call with id "openclaw:pixel-ods:pixel_ods_notify" and args ';
const REMINDER_END = ' It schedules the reminder and it reaches their phone then. No search is needed first. Never use sleep for this.';
export const REMINDER_HINT = REMINDER_CALL + '{"message": "<the reminder>", "inMinutes": <minutes from now>}, '
  + 'or {"message": "<the reminder>", "at": "<HH:MM>"} for a time of day.' + REMINDER_END;
export function reminderHint(text) {
  if (typeof text !== 'string' || !ASKS_REMINDER.test(text)) return '';
  const delay = text.match(DELAY);
  if (!delay) return REMINDER_HINT;
  const minutes = Number(delay[1]) * (/^h/i.test(delay[2]) ? 60 : 1);
  return REMINDER_CALL + JSON.stringify({message: '<the reminder>', inMinutes: minutes}) + '.' + REMINDER_END;
}

export function reminderJob(plan, agentId) {
  return {name: plan.name, description: plan.description, enabled: true, agentId,
    schedule: {kind: 'at', at: new Date(plan.atMs).toISOString()}, sessionTarget: 'isolated', wakeMode: 'now',
    payload: {kind: 'agentTurn', message: plan.runMessage, timeoutSeconds: 60, lightContext: true, toolsAllow: []},
    delivery: {mode: 'none'}, deleteAfterRun: true};
}

const sameSchedule = (job, plan) => job.schedule?.kind === 'cron' && job.schedule.expr === plan.expr
  && (job.schedule.tz ?? 'UTC') === plan.tz && job.payload?.message === plan.message
  && job.payload?.timeoutSeconds === plan.timeoutSeconds
  && JSON.stringify(job.payload?.toolsAllow ?? []) === JSON.stringify(plan.tools) && job.enabled !== false;

// Hooks for OpenClaw's cron: finished runs of Portal's tasks go to the integrations service,
// and the digest job follows the owner's digest settings.
export function createTaskHooks({submit = submitIntegration, agentId = 'pixel', warn = () => {}, timers = globalThis, shared = globalThis} = {}) {
  // The gateway's cron service. At gateway start it may not exist yet, so the start context's
  // getCron is kept and asked again when the service is needed, as OpenClaw's own plugins do.
  let found, getCron, timer;
  const remember = context => {
    if (typeof context?.getCron === 'function') getCron = shared[SHARED_CRON] = context.getCron;
    found = context?.getCron?.() ?? found;
  };
  const scheduler = () => (found ??= getCron?.() ?? shared[SHARED_CRON]?.());

  // One sync at a time: two that overlap could both find no digest job and both add one.
  let syncing;
  function syncDigest() {
    syncing ??= syncOnce().finally(() => { syncing = undefined; });
    return syncing;
  }

  async function syncOnce() {
    const cron = scheduler();
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

  // Called by the notify tool with a plan the service checked.
  async function scheduleReminder(plan) {
    const cron = scheduler();
    if (!cron) throw new Error('The scheduler is not ready yet. Try again in a minute.');
    await cron.add(reminderJob(plan, agentId));
  }

  async function reminderEvent(event, job) {
    if (event.action === 'started') {
      const reply = await submit({action: 'reminder-due', jobId: str(event.jobId), name: str(job.name), description: str(job.description)});
      if (!reply?.ok) warn(`Portal integrations: reminder not sent: ${reply?.message}`);
    } else if (event.action === 'finished' && scheduler()) {
      // The reminder went out when the job started; a failed run must not retry and send it again.
      await scheduler().remove(str(event.jobId));
    }
  }

  const syncQuietly = () => syncDigest().catch(error => warn(`Portal integrations: daily digest not scheduled: ${error.message}`));

  return {
    syncDigest,
    scheduleReminder,
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
      if (event?.agentId && event.agentId !== agentId) return;
      const job = event?.job ?? {};
      if (isReminder(job)) {
        try {
          await reminderEvent(event, job);
        } catch (error) {
          warn(`Portal integrations: reminder failed: ${error.message}`);
        }
        return;
      }
      if (event?.action !== 'finished') return;
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
