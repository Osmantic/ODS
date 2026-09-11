// Content-free host observations for the Pixel workbench. Tool arguments,
// output, paths, prompts and tokens never enter this projection.
const RUN = /^chatcmpl_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ORDER = ['read', 'agent', 'run', 'edit', 'browser', 'preview', 'action', 'unknown'];
const KINDS = new Map([
  ...['read', 'ls', 'glob', 'grep'].map(name => [name, 'read']),
  ...['write', 'edit', 'apply_patch'].map(name => [name, 'edit']),
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

function failedResult(event) {
  if (event?.error) return true;
  let result = event?.result;
  for (let depth = 0; depth < 3 && result && typeof result === 'object'; depth++) {
    const details = result.details;
    if (result.isError === true || ['failed', 'error', 'blocked'].includes(details?.status)
      || (Number.isInteger(details?.exitCode) && details.exitCode !== 0)) return true;
    result = details?.result;
  }
  return false;
}

export function createTaskActivity({now = () => new Date().toISOString(), maximumRuns = 64, maximumCalls = 512} = {}) {
  const runs = new Map();
  const identify = (event, context) => context?.agentId === 'pixel' ? context.runId ?? event?.runId : undefined;
  function begin(event, context) {
    const id = identify(event, context);
    if (!RUN.test(id ?? '')) return;
    if (runs.has(id)) return;
    while (runs.size >= maximumRuns) {
      const settled = [...runs].find(([, run]) => run.state !== 'running');
      if (!settled) return;
      runs.delete(settled[0]);
    }
    runs.set(id, {runId:id, startedAt:now(), finishedAt:null, state:'running', calls:new Map(), truncated:false});
  }
  function record(event, context, outcome) {
    const run = runs.get(identify(event, context));
    let callId = context?.toolCallId ?? event?.toolCallId;
    if (!run || typeof callId !== 'string' || !callId || callId.length > 256) return;
    const toolName = context?.toolName ?? event?.toolName;
    // The pinned Tool Search runtime emits hooks for both the outer tool_call
    // and its inner execution. Correlate its explicit parent ID, not arguments
    // or timing, so one invocation is not displayed as two calls.
    if (callId.startsWith('tool_search_code:') && typeof toolName === 'string') {
      const parents = [...run.calls].filter(([parentId, call]) => {
        if (!call.wrapped || call.kind !== kindFor(event, context)) return false;
        const parent = parentId.trim().replace(/[^A-Za-z0-9_.:-]+/g, '_').slice(0,120) || 'call';
        const prefix = `tool_search_code:${parent}:${toolName}:`;
        return callId.startsWith(prefix) && /^[1-9][0-9]*$/.test(callId.slice(prefix.length));
      });
      if (parents.length === 1) callId = parents[0][0];
    }
    const existing = run.calls.get(callId);
    if (!existing && run.calls.size >= maximumCalls) { run.truncated = true; return; }
    // A blocked attempt must not later become a successful effect because a
    // wrapper emitted an after-hook. Duplicate hook delivery is idempotent.
    if (existing?.outcome === 'blocked' || (existing && outcome === 'running' && existing.outcome !== 'running')) return;
    run.calls.set(callId, {kind:existing?.kind ?? kindFor(event, context), outcome, wrapped:existing?.wrapped ?? toolName === 'tool_call'});
  }
  return {
    begin,
    before(event, context, blocked = false) { record(event, context, blocked ? 'blocked' : 'running'); },
    after(event, context) { record(event, context, failedResult(event) ? 'failed' : 'completed'); },
    finish(event, context) {
      const run = runs.get(identify(event, context));
      if (!run) return;
      if (run.finishedAt) return;
      run.finishedAt = now();
      run.state = event?.success === true ? 'completed' : event?.success === false || event?.error ? 'failed' : 'finished';
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
      return {schemaVersion:1, runId:run.runId, startedAt:run.startedAt, finishedAt:run.finishedAt,
        state:run.state, calls:run.calls.size,
        failures:activities.reduce((sum, item) => sum + item.failures, 0),
        blocked:activities.reduce((sum, item) => sum + item.blocked, 0),
        truncated:run.truncated, activities};
    },
  };
}
