import {displayForActivity} from './activity-display.mjs';
// Bounded public host observations for the Pixel workbench. Only explicitly
// selected/filtered metadata and excerpts enter this projection. Token counts are
// optional numeric measurements from the final model response, never estimates.
const RUN = /^chatcmpl_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const noEarlierThan = (observed, floor) => observed < floor ? floor : observed;
const ORDER = ['read', 'agent', 'run', 'edit', 'browser', 'preview', 'action', 'unknown'];
const KINDS = new Map([
  ...['read', 'ls', 'glob', 'grep'].map(name => [name, 'read']),
  ...['write', 'edit', 'apply_patch', 'pixel_ods_workspace_bundle'].map(name => [name, 'edit']),
  ...['exec', 'process', 'shell', 'bash', 'eval', 'lsp', 'debug'].map(name => [name, 'run']),
  ...['task', 'hub', 'sessions_spawn', 'sessions_send'].map(name => [name, 'agent']),
  ...['browser', 'web_search', 'web_fetch', 'pixel_ods_web_extract', 'pixel_ods_research'].map(name => [name, 'browser']),
  ['pixel_ods_workspace_preview', 'preview'],
  ...['pixel_ods_status', 'pixel_ods_apps_list', 'pixel_ods_host_observe', 'pixel_ods_extensions'].map(name => [name, 'read']),
  ...['pixel_ops_run', 'pixel_ops_workflow_submit', 'pixel_ods_download_promote'].map(name => [name, 'action']),
]);

function kindFor(event, context) {
  let name = context?.toolName ?? event?.toolName;
  if (name === 'tool_call' && typeof event?.params?.id === 'string') {
    const id = event.params.id;
    name = id.startsWith('openclaw:core:') ? id.slice('openclaw:core:'.length) : id;
    // Extension IDs may be fully qualified; use only the closed name map.
    if (!KINDS.has(name) && /^[a-z0-9_-]+:[a-z0-9_-]+:[a-z0-9_-]+$/i.test(id)) name = id.split(':').at(-1);
  }
  return KINDS.get(name) ?? 'unknown';
}

// The goal partial-work settlement view (settlement below) accepts only these
// exact tools: a direct call by name, or a catalog call by its exact source
// (the pinned runtime's ids are openclaw:core:<tool> and
// openclaw:pixel-ods:<tool>; the plan contract also names the bare id).
// Discovery and the plan tool are 'unknown' in the public kinds above; that
// display schema is not widened. Everything else refuses the settlement.
const SETTLEMENT_CORE = new Set(['read', 'ls', 'glob', 'grep', 'write', 'edit', 'apply_patch', 'exec', 'process']);
const SETTLEMENT_PLUGIN = new Set(['pixel_ods_goal', 'pixel_ods_activity']);
const SETTLEMENT_DIRECT = new Set([...SETTLEMENT_CORE, ...SETTLEMENT_PLUGIN, 'tool_search', 'tool_describe']);
const SETTLEMENT_EDITS = new Set(['write', 'edit', 'apply_patch']);
const SETTLEMENT_EXECUTIONS = new Set(['exec', 'process']);
function settlementTool(selected) {
  if (!selected || typeof selected.name !== 'string') return undefined;
  if (!selected.wrapped) return SETTLEMENT_DIRECT.has(selected.name) ? selected.name : undefined;
  const [source, name] = selected.name.startsWith('openclaw:core:') ? ['core', selected.name.slice(14)]
    : selected.name.startsWith('openclaw:pixel-ods:') ? ['pixel-ods', selected.name.slice(19)] : [undefined, selected.name];
  if (source !== 'pixel-ods' && SETTLEMENT_CORE.has(name)) return name;
  if (source !== 'core' && SETTLEMENT_PLUGIN.has(name)) return name;
  return undefined;
}

// The parent part of a Tool Search child ID, as the pinned runtime sanitizes it
// (tool_search_code:<parent>:<tool>:<sequence>).
const childParent = parentId => parentId.trim().replace(/[^A-Za-z0-9_.:-]+/g, '_').slice(0,120) || 'call';

function failedResult(event) {
  if (event?.error) return true;
  let result = event?.result;
  for (let depth = 0; depth < 3 && result && typeof result === 'object'; depth++) {
    const details = result.details;
    if (result.isError === true || details?.ok === false || details?.success === false
      || ['failed', 'error', 'blocked'].includes(details?.status)
      || (Number.isInteger(details?.exitCode) && details.exitCode !== 0)) return true;
    result = details?.result;
  }
  return false;
}

export function createTaskActivity({agentId = 'pixel', now = () => new Date().toISOString(), maximumRuns = 64, maximumCalls = 512, goalForRun = () => null, projectsForSession = () => []} = {}) {
  const runs = new Map();
  const identify = (event, context) => context?.agentId === agentId ? context.runId ?? event?.runId : undefined;
  function knownRun(event, context) {
    const run=runs.get(identify(event,context));
    const key=context?.sessionKey;
    // Some HTTP turns acquire their session key only at the first tool hook.
    // A conflicting key must never reassign this run to another conversation.
    if(run && typeof key==='string' && key.startsWith(`agent:${agentId}:openai-user:ods-`)
        && /^[a-f0-9]{64}$/.test(key.slice(`agent:${agentId}:openai-user:ods-`.length))) {
      if(run.sessionKey && run.sessionKey!==key) {run.sessionConflict=true;run.sessionKey=undefined;}
      else if(!run.sessionConflict) run.sessionKey=key;
    }
    return run;
  }
  function begin(event, context) {
    const id = identify(event, context);
    if (!RUN.test(id ?? '')) return;
    if (runs.has(id)) {
      const run = knownRun(event, context);
      const freshPromptBuild = event && typeof event === 'object' && !run.promptBuildEvents.has(event);
      if (event && typeof event === 'object') run.promptBuildEvents.add(event);
      // OpenClaw ends each embedded attempt, including an empty successful
      // attempt followed by real work under the same request ID.
      // Only a fresh owned before_prompt_build may reopen a settled attempt.
      // Preserve all prior tool failures and the original request start time.
      if (freshPromptBuild && typeof event.prompt === 'string' && event.prompt.trim()
          && ['failed', 'completed', 'finished'].includes(run.state) && run.finishedAt && !run.sessionConflict
          && typeof run.sessionId === 'string' && run.sessionId
          && context?.sessionId === run.sessionId
          && typeof run.sessionKey === 'string' && run.sessionKey
          && context?.sessionKey === run.sessionKey) {
        run.state = 'running';
        run.finishedAt = null;
      }
      return;
    }
    while (runs.size >= maximumRuns) {
      const settled = [...runs].find(([, run]) => run.state !== 'running');
      if (!settled) return;
      runs.delete(settled[0]);
    }
    const promptBuildEvents = new WeakSet();
    if (event && typeof event === 'object') promptBuildEvents.add(event);
    runs.set(id, {runId:id, sessionId:context?.sessionId, sessionKey:context?.sessionKey, promptBuildEvents,
      workspaceRoot:context?.workspaceRoot,
      startedAt:now(), finishedAt:null, state:'running', calls:new Map(), truncated:false, context:null});
  }
  function record(event, context, outcome) {
    const run = knownRun(event, context);
    let callId = context?.toolCallId ?? event?.toolCallId;
    if (!run) return;
    // Not displayed, but never silently absent from the settlement either.
    if (typeof callId !== 'string' || !callId || callId.length > 256) { run.unidentifiedCall = true; return; }
    const toolName = context?.toolName ?? event?.toolName;
    // The pinned Tool Search runtime emits hooks for both the outer tool_call
    // and its inner execution. Correlate its explicit parent ID, not arguments
    // or timing, so one invocation is not displayed as two calls.
    const hookCallId = callId;
    if (callId.startsWith('tool_search_code:') && typeof toolName === 'string') {
      const parents = [...run.calls].filter(([parentId, call]) => {
        if (!call.wrapped || call.kind !== kindFor(event, context)) return false;
        const prefix = `tool_search_code:${childParent(parentId)}:${toolName}:`;
        return callId.startsWith(prefix) && /^[1-9][0-9]*$/.test(callId.slice(prefix.length));
      });
      if (parents.length === 1) callId = parents[0][0];
      // A child that two parents could own belongs to neither (settlement only).
      else if (parents.length > 1) run.ambiguousChild = true;
    } else if (toolName === 'tool_call' && !run.calls.has(callId) &&
        [...run.calls].some(([parentId, call]) => call.wrapped && childParent(parentId) === childParent(callId))) {
      // Two catalog calls whose child IDs would share one sanitized prefix.
      run.ambiguousChild = true;
    }
    const child = callId !== hookCallId;
    const existing = run.calls.get(callId);
    if (!existing && run.calls.size >= maximumCalls) { run.truncated = true; return; }
    // Settlement evidence, never projected: what every hook of this call
    // showed, kept even where the display outcome below is replaced.
    const evidence = existing?.evidence ?? {admitted:false, settled:false, failed:false, conflict:false,
      selected:{wrapped:toolName === 'tool_call', name:toolName === 'tool_call' ? event?.params?.id : toolName}};
    const hookName = toolName === 'tool_call' ? event?.params?.id : toolName;
    // A receipt without a result says nothing about what happened.
    if ((outcome === 'completed' || outcome === 'failed') && (event?.result === undefined || event?.result === null) &&
        !event?.error) evidence.unknown = true;
    if ((typeof context?.toolCallId === 'string' && typeof event?.toolCallId === 'string' && context.toolCallId !== event.toolCallId) ||
        (typeof context?.toolName === 'string' && typeof event?.toolName === 'string' && context.toolName !== event.toolName) ||
        (typeof context?.sessionId === 'string' && context.sessionId !== run.sessionId) ||
        // Any present session key, valid or not, must be the run's own.
        (typeof context?.sessionKey === 'string' && context.sessionKey !== run.sessionKey) ||
        (typeof event?.sessionKey === 'string' && event.sessionKey !== run.sessionKey) ||
        (typeof event?.runId === 'string' && event.runId !== run.runId) ||
        (typeof event?.sessionId === 'string' && event.sessionId !== run.sessionId) ||
        // A child names its parent's tool; an outer hook repeats its own selection.
        (child ? hookName !== settlementTool(evidence.selected) : hookName !== evidence.selected.name)) evidence.conflict = true;
    if (outcome === 'failed' || outcome === 'blocked') evidence.failed = true;
    if (!child && (outcome === 'running' || outcome === 'blocked')) {
      // A second admission after its receipt is not this call's evidence.
      if (existing && existing.outcome !== 'running') evidence.conflict = true;
      evidence.admitted = true;
    }
    if (!child && (outcome === 'completed' || outcome === 'failed')) evidence.settled = evidence.admitted;
    // A blocked attempt must not later become a successful effect because a
    // wrapper emitted an after-hook. Duplicate hook delivery is idempotent.
    if (existing?.outcome === 'blocked' || (existing && outcome === 'running' && existing.outcome !== 'running')) return;
    const display=outcome==='blocked' ? null : displayForActivity(event,{...context,workspaceRoot:run.workspaceRoot},existing?.display);
    // WSL/host clock sync can move wall time backwards between the tool hooks.
    // Keep the public receipt monotonic or ingress rejects the entire answer.
    const observedAt=now();
    const startedAt=existing?.startedAt ?? noEarlierThan(observedAt,run.startedAt);
    const finishedAt=outcome==='running' ? null : existing?.finishedAt ?? noEarlierThan(observedAt,startedAt);
    run.calls.set(callId, {kind:existing?.kind ?? kindFor(event, context), outcome, display, startedAt, finishedAt,
      wrapped:existing?.wrapped ?? toolName === 'tool_call', evidence});
  }
  return {
    begin,
    modelOutput(event, context) {
      const run = knownRun(event, context);
      if (!run) return;
      // event.usage is cumulative across tool turns; it is NOT occupancy.
      const usage = event?.lastAssistant?.usage;
      const valid = value => Number.isSafeInteger(value) && value >= 0 && value <= 10_000_000;
      const window = event?.contextTokenBudget;
      if (!valid(window) || window < 1 || !usage || !valid(usage.input) || !valid(usage.output)) return;
      if (['cacheRead','cacheWrite'].some(key => usage[key] !== undefined && !valid(usage[key]))) return;
      const used = usage.input + usage.output + (usage.cacheRead ?? 0) + (usage.cacheWrite ?? 0);
      // All-zero usage is a common sentinel from providers without telemetry.
      if (used > 0 && valid(used)) run.context = {used, window, measuredAt:noEarlierThan(now(),run.startedAt)};
    },
    activeForUser(user) {
      if (typeof user !== 'string' || !/^ods-[a-f0-9]{64}$/.test(user)) return null;
      const matches = [...runs.values()].filter(run => run.state === 'running' && run.sessionKey === `agent:${agentId}:openai-user:${user}`);
      // Ambiguity is not evidence: never guess which run belongs to this turn.
      return matches.length === 1 ? this.projection(matches[0].runId) : null;
    },
    before(event, context, blocked = false) { record(event, context, blocked ? 'blocked' : 'running'); },
    after(event, context) { record(event, context, failedResult(event) ? 'failed' : 'completed'); },
    finish(event, context) {
      const run = knownRun(event, context);
      if (!run) return;
      const endEvents = run.endEvents ??= new WeakSet();
      if (event && typeof event === 'object') {
        if (endEvents.has(event)) return;
        endEvents.add(event);
      }
      if (run.finishedAt) return;
      run.finishedAt = [...run.calls.values()].reduce((latest,call)=>
        noEarlierThan(latest,call.finishedAt ?? call.startedAt),noEarlierThan(now(),run.startedAt));
      run.state = event?.success === true ? 'completed' : event?.success === false || event?.error ? 'failed' : 'finished';
    },
    // Read-only settlement of one run's known calls for the goal partial-work
    // stop (tool-loop-guard.mjs): this run, bound to the caller's session,
    // complete (not truncated), every call admitted and settled by its own
    // hooks with no failure, block or identity conflict at any hook, and only
    // allowlisted tools. Lists every execution for the guard to match to its
    // own bound terminal receipts. Known-call settlement only: it cannot see
    // detached processes or effects outside these receipts.
    settlement(id, {sessionId, sessionKey} = {}) {
      const refuse = reason => ({schemaVersion:1, ok:false, reason});
      const run = runs.get(id);
      if (!run) return refuse('missing');
      if (run.sessionConflict || typeof sessionId !== 'string' || !sessionId || run.sessionId !== sessionId ||
          typeof sessionKey !== 'string' || !sessionKey || run.sessionKey !== sessionKey) return refuse('identity');
      if (run.truncated) return refuse('truncated');
      if (run.unidentifiedCall || run.ambiguousChild) return refuse('identity');
      let edits = 0;
      const executions = [];
      for (const [callId, call] of run.calls) {
        const evidence = call.evidence;
        if (!evidence || evidence.failed || call.outcome === 'failed' || call.outcome === 'blocked') return refuse('failed');
        if (evidence.conflict) return refuse('identity');
        if (!evidence.admitted || !evidence.settled || evidence.unknown || call.outcome !== 'completed') return refuse('unsettled');
        const tool = settlementTool(evidence.selected);
        if (!tool) return refuse('unknown-tool');
        if (SETTLEMENT_EDITS.has(tool)) edits++;
        if (SETTLEMENT_EXECUTIONS.has(tool)) executions.push({callId, tool});
      }
      return {schemaVersion:1, ok:true, calls:run.calls.size, edits, executions};
    },
    projection(id) {
      const run = runs.get(id);
      if (!run) return null;
      const groups = new Map();
      for (const call of run.calls.values()) {
        const group = groups.get(call.kind) ?? {kind:call.kind, calls:0, failures:0, blocked:0};
        group.calls++;
        if (['failed', 'blocked'].includes(call.outcome)) group.failures++;
        if (call.outcome === 'blocked') group.blocked++;
        groups.set(call.kind, group);
      }
      const activities = ORDER.filter(kind => groups.has(kind)).map(kind => groups.get(kind));
      const events = [...run.calls.values()].map((call, index) => ({sequence:index + 1, kind:call.kind,
        state:call.outcome, startedAt:call.startedAt, finishedAt:call.finishedAt, display:call.display})).slice(-24);
      return {schemaVersion:4, runId:run.runId, startedAt:run.startedAt, finishedAt:run.finishedAt,
        state:run.state, calls:run.calls.size,
        failures:activities.reduce((sum, item) => sum + item.failures, 0),
        blocked:activities.reduce((sum, item) => sum + item.blocked, 0),
        truncated:run.truncated, activities, events, context:run.context, goal:goalForRun(id),
        projects:projectsForSession(run.sessionKey).filter(project=>!run.finishedAt || project.observedAt<=run.finishedAt)};
    },
  };
}
