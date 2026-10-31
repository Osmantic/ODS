import {validateConnectionStatus} from './connections-status.mjs';

export const GOOGLE_LOOKUP_UNVERIFIED = 'Could not verify the requested Google lookup.';
const STATUS = 'pixel_ods_connections_status';
const SOURCES = new Map([
  ...['inbox','sent','search','read','thread','count'].map(name => [`pixel_gmail_${name}`,'gmail']),
  ...['list','get','count'].map(name => [`pixel_calendar_${name}`,'calendar']),
]);
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const integer = value => Number.isSafeInteger(value) && value >= 0;
const exact = (value,keys) => object(value) && Object.keys(value).length === keys.length
  && keys.every(key => Object.hasOwn(value,key));
const timestamp = value => {
  if (typeof value !== 'string' || value.length > 32) return false;
  const parts = /^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.\d{1,6})?Z$/.exec(value);
  if (!parts || Number(parts[1]) < 1) return false;
  const date = new Date(value);
  return [date.getUTCFullYear(),date.getUTCMonth()+1,date.getUTCDate(),date.getUTCHours(),
    date.getUTCMinutes(),date.getUTCSeconds()].every((part,index) => part === Number(parts[index+1]));
};
const iso = timestamp;
const COUNT_COMMON = ['schemaVersion','recordsReturned','generatedAt','ageSeconds','freshness',
  'syncState','queryApplied','limitations','readState','source','scope','filterSemantics'];
const GMAIL_COUNT = ['indexedMatchCount','mailboxTotal','canAnswerMailboxTotal','coverageState',
  'completeWithinConfiguredQueries','absenceWithinCoveredProjection'];
const CALENDAR_COUNT = ['observedMatchCount','completeMatchCount','requestCoveredByWindow',
  'completeWithinWindow','unresolvedEventCount','windowKind','windowState','windowStart','windowEnd',
  'windowDays','ownerTimeZone','calendarTimeZone'];
const LIMITATIONS = new Set(['source-clock-invalid','freshness-unverified','source-stale','sync-unverified',
  'source-partial','coverage-unknown','calendar-time-zone-inconsistent','calendar-time-zone-unavailable',
  'window-outside-coverage','unresolved-event-time','owner-time-zone-unavailable','window-unavailable',
  'invalid-window','window-too-large','ambiguous-civil-midnight']);
const optional = (value,valid) => value === null || valid(value);

// These receipts prove only that this source lookup returned a well-formed
// local snapshot. They never prove current OAuth validity or an account total.
function countProof(value,source) {
  if (!exact(value,[...COUNT_COMMON,...(source === 'gmail' ? GMAIL_COUNT : CALENDAR_COUNT)])
      || value.schemaVersion !== 1 || value.recordsReturned !== 0 || value.source !== source
      || value.readState !== 'ok' || !iso(value.generatedAt) || !optional(value.ageSeconds,integer)
      || !['fresh','stale','unknown'].includes(value.freshness)
      || !['ok','error','disabled','unknown'].includes(value.syncState)
      || typeof value.queryApplied !== 'boolean' || !Array.isArray(value.limitations)
      || value.limitations.length > 16 || value.limitations.some(item => !LIMITATIONS.has(item))
      || value.filterSemantics !== 'sanitized-projected-fields') return false;
  if (source === 'gmail') return value.scope === 'current-local-email-index'
    && integer(value.indexedMatchCount) && value.mailboxTotal === null && value.canAnswerMailboxTotal === false
    && ['partial-index','stale-index','coverage-unknown','complete-within-configured-queries'].includes(value.coverageState)
    && typeof value.completeWithinConfiguredQueries === 'boolean' && typeof value.absenceWithinCoveredProjection === 'boolean'
    && (!value.completeWithinConfiguredQueries || value.coverageState === 'complete-within-configured-queries'
      && value.freshness === 'fresh' && value.syncState === 'ok')
    && value.absenceWithinCoveredProjection === (value.completeWithinConfiguredQueries && value.indexedMatchCount === 0);
  return value.scope === 'configured-calendar-projection' && integer(value.observedMatchCount)
    && optional(value.completeMatchCount,integer) && optional(value.requestCoveredByWindow,v => typeof v === 'boolean')
    && typeof value.completeWithinWindow === 'boolean' && integer(value.unresolvedEventCount)
    && ['absolute','rolling','civil-days'].includes(value.windowKind) && value.windowState === 'resolved'
    && iso(value.windowStart) && iso(value.windowEnd) && Date.parse(value.windowStart) < Date.parse(value.windowEnd)
    && optional(value.windowDays,v => integer(v) && v >= 1 && v <= 730)
    && optional(value.ownerTimeZone,v => typeof v === 'string' && v.length > 0 && v.length <= 128)
    && optional(value.calendarTimeZone,v => typeof v === 'string' && v.length > 0 && v.length <= 128)
    && (value.completeWithinWindow ? value.completeMatchCount === value.observedMatchCount
      && value.requestCoveredByWindow === true && value.unresolvedEventCount === 0
      && value.freshness === 'fresh' && value.syncState === 'ok' : value.completeMatchCount === null);
}

function sourceProof(value,name,source) {
  if (!object(value)) return false;
  if (name.endsWith('_count')) return countProof(value,source);
  if (!iso(value.generatedAt) || typeof value.stale !== 'boolean' || !integer(value.ageSeconds)) return false;
  if (name === 'pixel_gmail_read' || name === 'pixel_calendar_get') return typeof value.id === 'string'
    && value.id.length > 0 && value.id.length <= 1024
    && (source === 'gmail' ? ['subject','summary','threadId'].every(key => typeof value[key] === 'string')
      : typeof value.title === 'string');
  const records = source === 'gmail' ? value.messages : value.events;
  return Array.isArray(records) && records.length <= 100 && records.every(item => object(item)
    && typeof item.id === 'string' && item.id.length > 0 && item.id.length <= 1024)
    && integer(value.totalMatches) && integer(value.count) && value.count === records.length
    && value.totalMatches >= value.count && typeof value.hasMore === 'boolean'
    && (source !== 'gmail' || integer(value.offset) && integer(value.limit) && value.limit >= 1 && value.limit <= 100);
}

function data(result) {
  if (!object(result) || result.isError === true) return undefined;
  if (object(result.details)) return result.details;
  if (!Array.isArray(result.content) || result.content.length !== 1 || result.content[0]?.type !== 'text'
      || typeof result.content[0].text !== 'string' || Buffer.byteLength(result.content[0].text) > 262144) return undefined;
  try { return JSON.parse(result.content[0].text); } catch { return undefined; }
}
function target(toolName,params) {
  const id = toolName === 'tool_call' ? params?.id : toolName;
  for (const name of [STATUS,...SOURCES.keys()]) {
    const sourceName = name === STATUS ? 'pixel-ods' : 'pixel-source-broker';
    if (id === name || toolName === 'tool_call' && id === `openclaw:${sourceName}:${name}`)
      return {name,sourceName,scope:SOURCES.get(name) ?? 'status'};
  }
  return undefined;
}

// Private, bounded and owned by one existing run entry. No request text,
// records or credential state is retained, and discovery is never evidence.
export function createConnectionEvidence() {
  const pending = new Map(), generations = new Map(), unresolved = new Set();
  return {
    prepare(binding) {
      const selected = target(binding.toolName,binding.params);
      if (!selected || typeof binding.callId !== 'string' || !binding.callId
          || typeof binding.runId !== 'string' || !binding.runId) return;
      const generation = (generations.get(selected.scope) ?? 0) + 1;
      generations.set(selected.scope,generation); unresolved.add(selected.scope);
      while (pending.size >= 128) pending.delete(pending.keys().next().value);
      pending.set(binding.callId,{...selected,generation,runId:binding.runId,
        sessionId:binding.sessionId,sessionKey:binding.sessionKey,transport:binding.toolName,
        target:binding.toolName === 'tool_call' ? binding.params.id : binding.toolName});
    },
    observe(binding,result,error) {
      const call = pending.get(binding.callId);
      if (!call || call.runId !== binding.runId || call.transport !== binding.toolName
          || call.sessionId !== binding.sessionId || call.sessionKey !== binding.sessionKey
          || (binding.toolName === 'tool_call' ? binding.params?.id : binding.toolName) !== call.target) return;
      pending.delete(binding.callId);
      if (call.generation !== generations.get(call.scope) || error || result?.isError === true) return;
      if (call.transport === 'tool_call') {
        const envelope = result?.details, tool = envelope?.tool;
        if (!object(tool) || tool.id !== `openclaw:${call.sourceName}:${call.name}`
            || tool.source !== 'openclaw' || tool.sourceName !== call.sourceName || tool.name !== call.name) return;
        result = envelope.result;
      }
      const value = data(result);
      if (call.scope === 'status') {
        try {
          const status = validateConnectionStatus(value);
          if (status.available && status.recordConfigured !== null && !['unknown','transitioning'].includes(status.connectionState))
            unresolved.delete('status');
        } catch { /* Malformed or unavailable is unknown, never disconnected. */ }
      } else if (sourceProof(value,call.name,call.scope)) unresolved.delete(call.scope);
    },
    unresolved: () => unresolved.size > 0,
  };
}
