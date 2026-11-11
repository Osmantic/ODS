import { submitIntegration } from './integrations.mjs';

// Managed job identity is held by the owner service; prose is never custody.
// A reminder is a one-off OpenClaw cron job too: the service sends it when the job starts, so it
// arrives on time however long the job's own agent turn takes.
const SYNC_MS = 60000;
const str = value => typeof value === 'string' ? value : '';
const equal = (left, right) => JSON.stringify(left) === JSON.stringify(right);
export const REMINDER_MARK = 'Portal reminder: ';
const isReminder = job => str(job?.description).startsWith(REMINDER_MARK);
// OpenClaw may load this plugin more than once in the gateway process: the instance whose
// hooks see gateway start is not always the one whose tools run. They share the scheduler here.
const SHARED_CRON = Symbol.for('ods.pixel-ods.getCron');

// The daily digest job, as the service describes it (integrations_apps.py DIGEST_MARKER, followed by
// its managed identity). Its run reads feeds anyone can write, unattended, so it may only read the
// digest (isDigestRun).
export const DIGEST_DESCRIPTION = 'Created by Portal integrations for the daily news digest.';
const DIGEST_JOBS = Symbol.for('ods.pixel-ods.digestJobs');
const digestJobs = shared => (shared[DIGEST_JOBS] ??= new Set());
const isDigestJob = job => str(job?.description).startsWith(DIGEST_DESCRIPTION);
// The job whose run a session is: OpenClaw names an isolated cron run's session agent:<agent>:cron:<job id>.
export function cronJobOfSession(sessionKey) {
  const match = typeof sessionKey === 'string' ? sessionKey.match(/^agent:[^:]+:cron:([^:]+)/i) : null;
  return match ? match[1].toLowerCase() : '';
}

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

// A turn that asks for a reminder gets a note naming the tool: pixel_ods_notify sits behind
// tool search, and without it a small model says it cannot schedule anything (#7794).
const ASKS_REMINDER = /\bremind(?:er|ers|ing)?\b|\blembr(?:e|ete|ar|ança|ete-me|a-me)\b/i;
// "Remind me what my deposit was": asking Portal to recall something, not to schedule it, unless
// the message also says when ("Remind me what to buy at 18:00").
const ASKS_RECALL = /\bremind\s+me\s+(?:what|who|whom|where|which|how|why|of|whether|if)\b/i;
const SAYS_WHEN = /\b(?:in|at|by)\s+\d|\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b|\b\d{1,2}:\d{2}\b|\b(?:tomorrow|tonight|later|every|daily|weekly)\b/i;
// The exact call, because a small model that only knows the tool's name searches and describes it
// again and again instead of calling it (e2e: three attempts for "Remind me in 3 minutes ...").
// The example uses the owner's own delay when the message gives one: a fixed example value is
// copied as is (e2e: "in 3 minutes" was scheduled for 10, the example's number).
const DELAY = /\b(?:in|daqui a|em)\s+(\d{1,4})\s*(min(?:ute|uto)?s?|h(?:ours?|oras?)?)\b/i;
const MORE_DELAY = /^\s*(?:,|and|e)?\s*(\d{1,4})\s*(min(?:ute|uto)?s?|m|h(?:ours?|oras?)?)\b/i;
const REMINDER_CALL = 'To remind the owner later, make one call: tool_call with id "openclaw:pixel-ods:pixel_ods_notify" and args ';
const REMINDER_END = ' It schedules the reminder and it reaches their phone then. No search is needed first. Never use sleep for this.';
export const REMINDER_HINT = REMINDER_CALL + '{"message": "<the reminder>", "inMinutes": <minutes from now>}, '
  + 'or {"message": "<the reminder>", "at": "<HH:MM>"} for a time of day.' + REMINDER_END;
export function reminderHint(text) {
  if (typeof text !== 'string' || !ASKS_REMINDER.test(text) || (ASKS_RECALL.test(text) && !SAYS_WHEN.test(text))) return '';
  const delay = text.match(DELAY);
  if (!delay) return REMINDER_HINT;
  // Every part of a delay such as "1 hour 30 minutes", "1h 30m" or "1 hora e 30 minutos": the model
  // copies the number as is, so the first part alone would fire it early.
  let minutes = Number(delay[1]) * (/^h/i.test(delay[2]) ? 60 : 1);
  let rest = text.slice(delay.index + delay[0].length);
  for (let part = rest.match(MORE_DELAY); part; part = rest.match(MORE_DELAY)) {
    minutes += Number(part[1]) * (/^h/i.test(part[2]) ? 60 : 1);
    rest = rest.slice(part[0].length);
  }
  return REMINDER_CALL + JSON.stringify({message: '<the reminder>', inMinutes: minutes}) + '.' + REMINDER_END;
}

export function reminderJob(plan, agentId) {
  return {name: plan.name, description: plan.description, enabled: true, agentId,
    schedule: {kind: 'at', at: new Date(plan.atMs).toISOString()}, sessionTarget: 'isolated', wakeMode: 'now',
    payload: {kind: 'agentTurn', message: plan.runMessage, timeoutSeconds: 60, lightContext: true, toolsAllow: []},
    delivery: {mode: 'none'}, deleteAfterRun: true};
}

export function createTaskHooks({submit = submitIntegration, agentId = 'pixel', warn = () => {}, timers = globalThis, shared = globalThis} = {}) {
  let timer, generation = 0, active = false, syncing;
  // The gateway's cron service. At gateway start it may not exist yet, so the start context's
  // getCron is kept and asked again when the service is needed, as OpenClaw's own plugins do.
  let found, getCron;
  const remember = context => {
    if (typeof context?.getCron === 'function') getCron = shared[SHARED_CRON] = context.getCron;
    found = context?.getCron?.() ?? found;
  };
  const scheduler = () => (found ??= getCron?.() ?? shared[SHARED_CRON]?.());
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
    if (owned) digestJobs(shared).add(str(owned.id).toLowerCase());
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
      digestJobs(shared).add(added.id.toLowerCase());
      await control('digest-job-record', identity, {jobId: added.id});
      return 'added';
    }
    if (sameSchedule(owned, wanted)) return 'kept';
    current(epoch);
    await service.update(owned.id, wanted);
    return 'updated';
  }
  function syncDigest() {
    const service = scheduler();
    if (!service) return Promise.resolve('no-cron');
    if (!active) return Promise.resolve('stopped');
    const epoch = generation;
    if (syncing?.epoch === epoch) return syncing.promise;
    const previous = syncing?.promise;
    const record = {epoch, promise: undefined};
    record.promise = Promise.resolve(previous).catch(() => {}).then(() => syncOnce(epoch, service))
      .finally(() => { if (syncing === record) syncing = undefined; });
    syncing = record;
    return record.promise;
  }
  const syncQuietly = () => syncDigest().catch(error => warn(`Portal integrations: daily digest not scheduled: ${error.message}`));

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

  // Whether a session is a run of the daily digest. A scheduled run this cannot identify counts as
  // one (it can still read the digest); a Portal chat or another session never does.
  async function isDigestRun(sessionKey) {
    const id = cronJobOfSession(sessionKey);
    if (!id) return false;
    if (digestJobs(shared).has(id)) return true;
    const cron = scheduler();
    if (!cron) return true;
    const job = (await cron.list({includeDisabled: true})).find(item => str(item.id).toLowerCase() === id);
    if (!job) return true;
    if (isDigestJob(job)) digestJobs(shared).add(id);
    return isDigestJob(job);
  }

  return {
    syncDigest,
    scheduleReminder,
    isDigestRun,
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
      if (owner !== agentId) return;
      const job = event.job ?? {};
      // Known before its run calls a tool: the job starts first.
      if (isDigestJob(job) && event?.jobId) digestJobs(shared).add(str(event.jobId).toLowerCase());
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
          runAtMs: Number.isFinite(event.runAtMs) ? event.runAtMs : 0,
          sessionId: str(event.sessionId), sessionKey: str(event.sessionKey), runId: str(event.runId)});
        if (!reply?.ok) warn(`Portal integrations: scheduled task notice failed: ${reply?.message}`);
      } catch (error) { warn(`Portal integrations: scheduled task notice failed: ${error.message}`); }
    },
  };
}
