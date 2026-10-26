import {readBoundedBytes} from './pixelArtifacts'

export class ScheduleFailure extends Error {
  constructor(code) { super(code); this.code=code }
}

export function newScheduleRequestId() {
  // Portal also runs over plain HTTP on a private LAN. randomUUID requires a
  // secure context there; getRandomValues remains available and preserves the
  // same random UUIDv4 identity rather than weakening it to Math.random.
  if (typeof globalThis.crypto?.randomUUID==='function') return globalThis.crypto.randomUUID()
  const bytes=globalThis.crypto.getRandomValues(new Uint8Array(16))
  bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128
  const hex=Array.from(bytes,byte=>byte.toString(16).padStart(2,'0')).join('')
  return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`
}
const keys=(value,expected)=>value && typeof value==='object' && !Array.isArray(value)
  && Object.keys(value).sort().join()===expected.slice().sort().join()
const id=value=>typeof value==='string' && /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(value)
const number=value=>Number.isSafeInteger(value) && value>=0
const text=(value,max)=>typeof value==='string' && new TextEncoder().encode(value).length<=max
function task(value) {
  if (!keys(value,['id','name','task','revision','enabled','schedule','createdAt','updatedAt','nextRunAt','lastRunAt','runningSince','lastStatus','externalDelivery','removed','removalPending'])
      || !id(value.id) || !text(value.name,1024) || !text(value.task,65536) || !/^[a-f0-9]{64}$/.test(value.revision)
      || ['enabled','externalDelivery','removed','removalPending'].some(key=>typeof value[key]!=='boolean')
      || ['createdAt','updatedAt','nextRunAt','lastRunAt','runningSince'].some(key=>value[key]!==null && !number(value[key]))
      || ![null,'ok','error','skipped'].includes(value.lastStatus)) throw new ScheduleFailure('scheduler-unavailable')
  const schedule=value.schedule
  if (!(keys(schedule,['kind','at']) && schedule.kind==='at' && text(schedule.at,256) && Number.isFinite(Date.parse(schedule.at)))
      && !(keys(schedule,['kind','expr','tz']) && schedule.kind==='cron' && text(schedule.expr,256) && (schedule.tz===null || text(schedule.tz,256)))
      && !(keys(schedule,['kind','everyMs','anchorMs']) && schedule.kind==='every' && number(schedule.everyMs) && (schedule.anchorMs===null || number(schedule.anchorMs)))) {
    throw new ScheduleFailure('scheduler-unavailable')
  }
  return value
}

export function scheduleReply(request,value) {
  const fail=()=>{throw new ScheduleFailure('scheduler-unavailable')}
  const kind=['list','archived'].includes(request.action)?'tasks':request.action==='runs'?'runs':'change'
  if (value?.kind!==kind) fail()
  if (kind==='change') {
    if (!keys(value,['kind','outcome','task']) || !['applied','unchanged','unconfirmed'].includes(value.outcome)
        || (value.task===null && value.outcome==='applied')) fail()
    if (value.task!==null) {
      task(value.task)
      if (['remove','set-enabled'].includes(request.action) && value.task.id!==request.id) fail()
    }
  } else {
    const fields=['kind','items','offset','hasMore','nextOffset',...(kind==='tasks'?['pendingCreates','pendingCount','pendingRemovals']:['taskId'])]
    if (!keys(value,fields) || value.offset!==request.offset || typeof value.hasMore!=='boolean'
        || (value.hasMore?(!number(value.nextOffset) || value.nextOffset<=value.offset):value.nextOffset!==null)
        || !Array.isArray(value.items) || value.items.length>(kind==='tasks'?10:1)) fail()
    if (kind==='tasks') {
      value.items.forEach(task)
      if (!Array.isArray(value.pendingCreates) || value.pendingCreates.length>10 || !number(value.pendingCount)
          || value.pendingCount<value.pendingCreates.length || !Array.isArray(value.pendingRemovals) || value.pendingRemovals.length>10) fail()
      value.pendingRemovals.forEach(task)
      for (const item of value.pendingCreates) {
        if (!keys(item,['requestId','name','createdAt']) || !/^[a-f0-9-]{36}$/.test(item.requestId) || !text(item.name,160) || !number(item.createdAt)) fail()
      }
    } else {
      if (value.taskId!==request.id) fail()
      for (const item of value.items) {
        if (!keys(item,['timestamp','status','summary','summaryTruncated','durationMs']) || !number(item.timestamp)
            || !['ok','error','skipped','unknown'].includes(item.status) || !text(item.summary,262144)
            || typeof item.summaryTruncated!=='boolean' || (item.durationMs!==null && !number(item.durationMs))) fail()
      }
    }
  }
  return value
}

export async function requestSchedules(request,signal) {
  const response=await fetch('/api/pixel/schedules',{method:'POST',cache:'no-store',signal,
    headers:{'Content-Type':'application/json'},body:JSON.stringify(request)})
  if (!response.ok) {
    let code
    try {code=(await response.json())?.detail?.code} catch {code=null}
    throw new ScheduleFailure(typeof code==='string' && /^[a-z][a-z0-9-]{0,95}$/.test(code)?code:'scheduler-unavailable')
  }
  const raw=await readBoundedBytes(response,524288)
  return scheduleReply(request,JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(raw)))
}

export function scheduleLabel(schedule) {
  if (schedule.kind==='at') return new Date(schedule.at).toLocaleString()
  if (schedule.kind==='every') return `Every ${Math.round(schedule.everyMs/60000)} minutes`
  const match=/^(\d{1,2}) (\d{1,2}) \* \* (\*|1-5|[0-6])$/.exec(schedule.expr)
  if (!match) return `Custom schedule${schedule.tz?` · ${schedule.tz}`:''}`
  const [,minute,hour,day]=match
  const frequency=day==='*'?'Daily':day==='1-5'?'Weekdays':`Every ${['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'][Number(day)]}`
  return `${frequency} at ${hour.padStart(2,'0')}:${minute.padStart(2,'0')}${schedule.tz?` · ${schedule.tz}`:''}`
}

export function scheduleError(error) {
  const code=error?.code?.replace(/^controller-/,'')
  return {
    'scheduler-update-required':'Update your installed ODS to use scheduled tasks.',
    'scheduler-platform-unavailable':'Scheduled tasks are not available on this installation yet.',
    'task-changed':'This task changed elsewhere. Refresh before making another change.',
    'task-time-passed':'Choose a time in the future.',
    'task-time-too-far':'Choose a time within the next 10 years.',
    'invalid-task-timezone':'This timezone is not supported by the runtime. Check your system timezone.',
    'runtime-change-pending':'Finish the pending runtime change before starting another task.',
    'task-history-full':'The local task history is full. Existing tasks have been preserved.',
    'invalid-schedule-state':'Saved task state needs repair. It has been preserved.',
    'unsafe-schedule-state':'Saved task state could not be verified. It has been preserved.',
  }[code] || 'ODS could not confirm the result. Check the task list before making another change.'
}
