// Closed, content-free projection. This is telemetry, never proof of task success.
export function parseTaskActivity(value, runId) {
  const keys = (item, expected) => item && typeof item === 'object' && !Array.isArray(item)
    && Object.keys(item).sort().join(',') === expected.split(',').sort().join(',');
  const timestamp = item => typeof item === 'string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/.test(item)
    && Number.isFinite(Date.parse(item)) && new Date(item).toISOString() === item;
  const count = item => Number.isInteger(item) && item >= 0 && item <= 512;
  if (!keys(value,'schemaVersion,runId,startedAt,finishedAt,state,calls,failures,blocked,truncated,activities')
    || value.schemaVersion !== 1 || value.runId !== runId
    || typeof runId !== 'string' || !/^chatcmpl_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(runId)
    || !timestamp(value.startedAt) || !['running','completed','failed','finished'].includes(value.state)
    || (value.state === 'running' ? value.finishedAt !== null : !timestamp(value.finishedAt) || value.finishedAt < value.startedAt)
    || !count(value.calls) || !count(value.failures) || !count(value.blocked)
    || typeof value.truncated !== 'boolean' || !Array.isArray(value.activities) || value.activities.length > 8) return null;
  const seen = new Set();
  let calls = 0, failures = 0, blocked = 0;
  for (const item of value.activities) {
    if (!keys(item,'kind,calls,failures,blocked') || !['read','agent','run','edit','browser','preview','action','unknown'].includes(item.kind)
      || seen.has(item.kind) || !count(item.calls) || item.calls === 0 || !count(item.failures) || !count(item.blocked)
      || item.blocked > item.failures || item.failures > item.calls) return null;
    seen.add(item.kind); calls += item.calls; failures += item.failures; blocked += item.blocked;
  }
  return calls === value.calls && failures === value.failures && blocked === value.blocked ? value : null;
}

export function parseTaskActivityFrame(frame) {
  if (frame?.object === 'ods.task.activity' && Object.keys(frame).sort().join(',') === 'id,object,pixel_task') {
    const task = parseTaskActivity(frame.pixel_task, frame.id);
    return task?.state === 'running' ? task : null;
  }
  if (frame?.choices?.[0]?.finish_reason !== 'stop') return null;
  return parseTaskActivity(frame.pixel_task, frame.id);
}
