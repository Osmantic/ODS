/* eslint-disable no-control-regex -- Reject control bytes in untrusted public input. */
// Optional display metadata is a closed, size-bounded public shape.
export function validActivityDisplay(value) {
  if(value===null)return true;
  const exact=(o,keys)=>o && typeof o==='object' && !Array.isArray(o) && Object.keys(o).sort().join(',')===keys.split(',').sort().join(',');
  const text=(s,n)=>typeof s==='string' && s.trim().length>0 && s.length<=n && !/[\u0000-\u001f\u007f]/.test(s);
  if(!exact(value,'type,label,detail,sources,steps,change') || !['text','search','tool','trace','steps'].includes(value.type) || !text(value.label,160)
    || (value.detail!==null && !text(value.detail,400)) || !Array.isArray(value.sources) || value.sources.length>3 || !Array.isArray(value.steps) || value.steps.length>8)return false;
  for(const source of value.sources) {
    if(!exact(source,'title,url') || !text(source.title,120) || !text(source.url,512))return false;
    try {const url=new URL(source.url);if(!['http:','https:'].includes(url.protocol) || url.username || url.password)return false;}catch{return false;}
  }
  const ids=new Set();
  for(const step of value.steps) {
    if(!exact(step,'id,title,status') || typeof step.id!=='string' || !/^[a-z][a-z0-9_]{0,31}$/.test(step.id) || ids.has(step.id) || !text(step.title,160) || !['pending','running','completed','blocked'].includes(step.status))return false;
    ids.add(step.id);
  }
  if(value.change!==null) {
    const c=value.change, code=s=>typeof s==='string' && s.length<=1000 && !/[\u0000-\u0008\u000b-\u001f\u007f]/.test(s);
    if(value.type!=='tool' || !exact(c,'file,kind,before,after,truncated') || !text(c.file,120) || !['write','edit','patch'].includes(c.kind) || !code(c.before) || !code(c.after) || typeof c.truncated!=='boolean')return false;
  }
  return (value.type==='search' || !value.sources.length) && (value.type==='steps' || !value.steps.length);
}

// Closed projection of observations and explicitly public progress. This is telemetry, never proof of task success.
export function parseTaskActivity(value, runId) {
  const keys = (item, expected) => item && typeof item === 'object' && !Array.isArray(item)
    && Object.keys(item).sort().join(',') === expected.split(',').sort().join(',');
  const timestamp = item => typeof item === 'string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/.test(item)
    && Number.isFinite(Date.parse(item)) && new Date(item).toISOString() === item;
  const count = item => Number.isInteger(item) && item >= 0 && item <= 512;
  const extended = [2,3,4].includes(value?.schemaVersion);
  const projects = value?.schemaVersion === 4;
  if (!keys(value,'schemaVersion,runId,startedAt,finishedAt,state,calls,failures,blocked,truncated,activities' + (extended ? ',events,context,goal' : '') + (projects ? ',projects' : ''))
    || ![1,2,3,4].includes(value.schemaVersion) || value.runId !== runId
    || typeof runId !== 'string' || !/^chatcmpl_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(runId)
    || !timestamp(value.startedAt) || !['running','completed','failed','finished'].includes(value.state)
    || (value.state === 'running' ? value.finishedAt !== null : !timestamp(value.finishedAt) || value.finishedAt < value.startedAt)
    || !count(value.calls) || !count(value.failures) || !count(value.blocked)
    || typeof value.truncated !== 'boolean' || !Array.isArray(value.activities) || value.activities.length > 8) return null;
  if (projects) {
    if (!Array.isArray(value.projects) || value.projects.length > 8) return null;
    const directories = new Set();
    for (const project of value.projects) {
      if (!keys(project,'schemaVersion,kind,relativeDirectory,observedAt')
        || project.schemaVersion !== 1 || project.kind !== 'ods-workspace-project'
        || typeof project.relativeDirectory !== 'string'
        || !/^Playground\/[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(project.relativeDirectory)
        || project.relativeDirectory.endsWith('.')
        || /^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$/i.test(project.relativeDirectory.slice('Playground/'.length))
        || directories.has(project.relativeDirectory) || !timestamp(project.observedAt)
        || (value.finishedAt !== null && project.observedAt > value.finishedAt)) return null;
      directories.add(project.relativeDirectory);
    }
  }
  if (extended) {
    if (value.goal !== null) {
      const goal=value.goal, ids=new Set();
      const text=(s,n)=>typeof s==='string' && s.trim().length>0 && s.length<=n && !/[\u0000-\u001f\u007f]/.test(s);
      if (!keys(goal,'status,summary,steps') || !['active','completed','blocked','waiting'].includes(goal.status)
        || !text(goal.summary,300) || !Array.isArray(goal.steps) || goal.steps.length>8) return null;
      for(const step of goal.steps) {
        if(!keys(step,'id,title,status') || typeof step.id !== 'string' || !/^[a-z][a-z0-9_]{0,31}$/.test(step.id) || ids.has(step.id)
          || !text(step.title,160) || !['pending','running','completed','blocked'].includes(step.status))return null;
        ids.add(step.id);
      }
      if(goal.status==='completed' && (!goal.steps.length || goal.steps.some(step=>step.status!=='completed')))return null;
    }
    if (!Array.isArray(value.events) || value.events.length !== Math.min(value.calls,24)) return null;
    let sequence = value.calls - value.events.length;
    for (const event of value.events) {
      if (!keys(event,'sequence,kind,state,startedAt,finishedAt'+(value.schemaVersion>=3?',display':'')) || (value.schemaVersion>=3 && !validActivityDisplay(event.display)) || event.sequence !== ++sequence
        || !['read','agent','run','edit','browser','preview','action','unknown'].includes(event.kind)
        || !['running','completed','failed','blocked'].includes(event.state)
        || !timestamp(event.startedAt) || event.startedAt < value.startedAt
        || (event.state === 'running' ? event.finishedAt !== null : !timestamp(event.finishedAt) || event.finishedAt < event.startedAt)) return null;
    }
    const context = value.context;
    const tokens = n => Number.isSafeInteger(n) && n >= 1 && n <= 10_000_000;
    if (context !== null && (!keys(context,'used,window,measuredAt') || !tokens(context.used)
      || !tokens(context.window) || !timestamp(context.measuredAt) || context.measuredAt < value.startedAt)) return null;
  }
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

// Retain the independent observations of a small team. These are grouping
// metadata, never a synthesized activity run or permission to resume a job.
export function parseProjectTasks(value) {
  if (!Array.isArray(value) || value.length > 6) return null;
  const ids = new Set();
  for (const task of value) {
    if (task?.schemaVersion !== 4 || !parseTaskActivity(task, task.runId)
      || !task.projects.length || ids.has(task.runId)) return null;
    ids.add(task.runId);
  }
  return value;
}
