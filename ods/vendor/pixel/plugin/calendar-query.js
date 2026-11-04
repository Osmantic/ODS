// Calendar projection queries. Absence of events proves nothing outside the synced window.
import { canonicalTimestamp, countMetadata, unavailableCount, COUNT_KINDS } from "./count-metadata.js";

const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

function civilDate(value) {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value ? date : null;
}

function timeLabel(bound, exclusiveEnd) {
  if (!bound || typeof bound !== "object") return null;
  if (bound.dateTime === undefined) {
    const date = civilDate(bound.date);
    return date ? `${WEEKDAYS[date.getUTCDay()]} ${bound.date} (${exclusiveEnd ? "exclusive all-day end" : "all day"})` : null;
  }
  // Never let the machine's timezone interpret a floating source timestamp,
  // or Date's permissive parser silently turn February 30 into another day.
  const match = typeof bound.dateTime === "string" && /^(\d{4}-\d{2}-\d{2})T((?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d)(?:\.\d+)?(Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$/.exec(bound.dateTime);
  if (!match || !civilDate(match[1])) return null;
  const instant = new Date(bound.dateTime);
  if (!Number.isFinite(instant.getTime())) return null;
  if (bound.timeZone === undefined || bound.timeZone === null) {
    // RFC 3339 -00:00 explicitly leaves the local offset unknown.
    if (match[3] === "-00:00") return null;
    const zone = ["Z", "+00:00"].includes(match[3]) ? "UTC" : `UTC${match[3]}`;
    return `${WEEKDAYS[civilDate(match[1]).getUTCDay()]} ${match[1]} ${match[2]} ${zone}`;
  }
  if (typeof bound.timeZone !== "string" || !bound.timeZone || bound.timeZone.length > 128 || match[1].startsWith("0000")) return null;
  let formatter;
  try {
    formatter = new Intl.DateTimeFormat("en-US", {calendar:"gregory",numberingSystem:"latn",timeZone:bound.timeZone,
      year:"numeric",month:"2-digit",day:"2-digit",weekday:"long",hour:"2-digit",minute:"2-digit",second:"2-digit",
      hourCycle:"h23",timeZoneName:"longOffset"});
  } catch (error) {
    if (!(error instanceof RangeError)) throw error;
    return null; // An invalid source timezone leaves the original event readable.
  }
  const parts = Object.fromEntries(formatter.formatToParts(instant).map(({type,value})=>[type,value]));
  const zone = formatter.resolvedOptions().timeZone === "UTC" ? "UTC"
    : `${bound.timeZone} (${parts.timeZoneName.replace(/^GMT/, "UTC")})`;
  return `${parts.weekday} ${parts.year.padStart(4,"0")}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second} ${zone}`;
}

// Labels are derived at read time, never trusted from the external projection.
// Keep raw source timestamps and coverage unchanged for verification.
export function calendarEvent(record) {
  const event = {...record};
  delete event.timeWindowUnverified;
  delete event.startLabel;
  delete event.endLabel;
  const start = timeLabel(event.start, false);
  const end = timeLabel(event.end, true);
  if (start) event.startLabel = start;
  if (end) event.endLabel = end;
  return event;
}

function eventBounds(record) {
  const start = Date.parse(record.start?.dateTime ?? record.start?.date ?? "");
  const end = Date.parse(record.end?.dateTime ?? record.end?.date ?? "");
  return [start, Number.isFinite(end) && end > start ? end : start];
}

// An event that began before the request and is still running counts, as does
// a multi-day all-day event. A point-in-time event counts when it falls inside.
export function overlapsRequest(record, from, to) {
  const [start, end] = eventBounds(record);
  if (!Number.isFinite(start)) return false;
  return overlapsBounds(start, end, from, to);
}

function overlapsBounds(start, end, from, to) {
  return end > start ? start < to && end > from : start >= from && start < to;
}

function matchesQuery(item, query) {
  return !query || [item.title, item.notesSummary, item.location].join(" ").toLowerCase().includes(query);
}

// A query has no independent timezone field. Never let the host timezone or
// Date.parse's normalization choose the owner's requested interval for them.
function queryInstant(value) {
  const match = typeof value === "string" && value.length <= 64
    && /^(\d{4}-\d{2}-\d{2})T(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d+)?)?(Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$/.exec(value);
  if (!match || !civilDate(match[1]) || match[2] === "-00:00") return NaN;
  return Date.parse(value);
}

export function calendarWindowCovers(coverage, timeMin, timeMax) {
  const bounds = [coverage?.windowStart, coverage?.windowEnd, timeMin, timeMax].map(queryInstant);
  if (!bounds.every(Number.isFinite)) return null;
  const [start, end, from, to] = bounds;
  if (end <= start || to <= from) return null;
  return from >= start && to <= end;
}

// Internal callers may retain the old explicit-instant shape. The native tool
// uses the shared typed window; no caller may mix the two argument contracts.
export function calendarListParameters(params) {
  if (!params || typeof params !== "object" || Array.isArray(params)
      || Object.keys(params).some(key => !["window", "timeMin", "timeMax", "query", "maxResults"].includes(key))) {
    throw new Error("Invalid Calendar list parameters. Read tool_describe before retrying.");
  }
  if (params.query !== undefined && (typeof params.query !== "string" || params.query.length > 500)
      || params.maxResults !== undefined && (!Number.isInteger(params.maxResults) || params.maxResults < 1 || params.maxResults > 100)) {
    throw new Error("Invalid Calendar list query or maxResults. Read tool_describe before retrying.");
  }
  const legacy = Object.hasOwn(params, "timeMin") || Object.hasOwn(params, "timeMax");
  if (Object.hasOwn(params, "window")) {
    if (legacy) throw new Error("Supply window only; do not mix window with timeMin/timeMax.");
    return params;
  }
  for (const key of ["timeMin", "timeMax"]) if (!Number.isFinite(queryInstant(params[key]))) {
    throw new Error(`${key} must be a valid full ISO 8601 timestamp with date and Z or an explicit UTC offset. Read tool_describe before retrying.`);
  }
  if (Date.parse(params.timeMax) <= Date.parse(params.timeMin)) throw new Error("timeMax must be later than timeMin.");
  const {timeMin, timeMax, ...rest} = params;
  return {...rest, window: {kind: "absolute", timeMin, timeMax}};
}

export function listCalendar(data, params, limit, sync, now = Date.now(), staleAfterMs = 180000, ownerTimeZone) {
  const {reply, candidates} = selectCalendar(data, calendarListParameters(params), sync, now, staleAfterMs, ownerTimeZone);
  const events = candidates.slice(0, limit).map(({record, unverified}) => {
    const event = calendarEvent(record);
    if (unverified) event.timeWindowUnverified = true;
    return event;
  });
  const coverage = data?.coverage ?? null;
  const uncertain = reply.windowState !== "resolved" || reply.unresolvedEventCount > 0
    || reply.freshness !== "fresh" || reply.syncState !== "ok";
  return {...reply, recordsReturned: events.length, stale: reply.freshness !== "fresh",
    totalMatches: reply.observedMatchCount === null ? null : candidates.length,
    count: events.length, hasMore: candidates.length > events.length, events,
    coverage: uncertain && coverage?.completeWithinWindow ? {...coverage, completeWithinWindow: false} : coverage};
}

export function canonicalTimeZone(value) {
  if (typeof value !== "string" || !/^[A-Za-z][A-Za-z0-9_+\-/]{0,127}$/.test(value)) return null;
  try { return new Intl.DateTimeFormat("en", { timeZone: value }).resolvedOptions().timeZone; }
  catch { return null; }
}

function civilResolver() {
  const formats = new Map(), dates = new Map();
  function parts(time, zone) {
    if (!formats.has(zone)) formats.set(zone, new Intl.DateTimeFormat("en-US", {
      timeZone: zone, calendar: "gregory", numberingSystem: "latn", hourCycle: "h23",
      year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }));
    const value = Object.fromEntries(formats.get(zone).formatToParts(time).map(({ type, value }) => [type, value]));
    return { date: `${value.year.padStart(4, "0")}-${value.month}-${value.day}`,
      clock: `${value.hour}:${value.minute}:${value.second}` };
  }
  function midnight(date, zone) {
    const key = `${zone}:${date}`;
    if (dates.has(key)) return dates.get(key);
    if (!civilDate(date) || date.startsWith("0000")) return null;
    const naive = Date.parse(`${date}T00:00:00Z`), offsets = new Set(), candidates = new Set();
    // Enumerate nearby ICU offsets, then round-trip candidates. Gaps, folds and
    // skipped dates have no unique midnight and are deliberately unavailable.
    for (let hours = -48; hours <= 48; hours += 1) {
      const at = naive + hours * 3_600_000, local = parts(at, zone);
      offsets.add(Date.parse(`${local.date}T${local.clock}Z`) - at);
    }
    for (const offset of offsets) {
      const candidate = naive - offset, local = parts(candidate, zone);
      if (local.date === date && local.clock === "00:00:00") candidates.add(candidate);
    }
    const result = candidates.size === 1 ? [...candidates][0] : null;
    dates.set(key, result);
    return result;
  }
  return { parts, midnight };
}

function addCivilDays(date, days) {
  const value = civilDate(date);
  if (!value) return null;
  value.setUTCDate(value.getUTCDate() + days);
  const result = value.toISOString().slice(0, 10);
  return civilDate(result) && !result.startsWith("0000") ? result : null;
}

export function resolveCountWindow(window, now, ownerTimeZone, resolver = civilResolver()) {
  const owner = canonicalTimeZone(ownerTimeZone);
  const result = { windowKind: COUNT_KINDS.has(window?.kind) ? window.kind : null, windowState: "unavailable",
    windowStart: null, windowEnd: null, windowDays: null, ownerTimeZone: owner,
    reason: "invalid-window", from: null, to: null };
  let from, to;
  const allowed = window?.kind === "absolute" ? ["kind", "timeMin", "timeMax"]
    : window?.kind === "rolling" ? ["kind", "days"] : ["kind", "days", "date", "start"];
  if (!window || typeof window !== "object" || Array.isArray(window)
      || Object.keys(window).some(key => !allowed.includes(key))) return result;
  if (window?.kind === "absolute") {
    const min = canonicalTimestamp(window.timeMin), max = canonicalTimestamp(window.timeMax);
    if (min === null || max === null) return result;
    from = Date.parse(min); to = Date.parse(max);
  } else if (["rolling", "civil-days"].includes(window?.kind)
      && Number.isInteger(window.days) && window.days >= 1 && window.days <= 730 && Number.isFinite(now)) {
    result.windowDays = window.days;
    if (window.kind === "rolling") { from = now; to = now + window.days * 86_400_000; }
    else {
      const dated = Object.hasOwn(window, "date");
      if (dated ? Object.hasOwn(window, "start") || !civilDate(window.date)
        : !["today", "tomorrow"].includes(window.start)) return result;
      if (!owner) return { ...result, reason: "owner-time-zone-unavailable" };
      // The model supplies an ISO civil date, never an inferred UTC offset.
      // Resolve every boundary using the same configured owner zone as today.
      const first = dated ? window.date
        : addCivilDays(resolver.parts(now, owner).date, window.start === "tomorrow" ? 1 : 0);
      const last = first && addCivilDays(first, window.days);
      if (!last) return result;
      for (let day = 0; first && day <= window.days; day += 1) {
        const date = addCivilDays(first, day);
        if (!date || resolver.midnight(date, owner) === null) return result;
      }
      from = first && resolver.midnight(first, owner); to = last && resolver.midnight(last, owner);
    }
  }
  if (!Number.isFinite(from) || !Number.isFinite(to) || to <= from) return result;
  return { ...result, windowState: "resolved", windowStart: new Date(from).toISOString(),
    windowEnd: new Date(to).toISOString(), reason: null, from, to };
}

function strictEventBounds(record, zone, resolver) {
  if (!record || typeof record !== "object" || !record.start || !record.end) return null;
  const allDay = record.start.dateTime === undefined && record.end.dateTime === undefined;
  let from, to;
  if (allDay) {
    if (!zone || !civilDate(record.start.date) || !civilDate(record.end.date)) return null;
    from = resolver.midnight(record.start.date, zone); to = resolver.midnight(record.end.date, zone);
  } else {
    if (record.start.date !== undefined || record.end.date !== undefined) return null;
    const start = canonicalTimestamp(record.start.dateTime), end = canonicalTimestamp(record.end.dateTime);
    if (start === null || end === null) return null;
    from = Date.parse(start); to = Date.parse(end);
  }
  return Number.isFinite(from) && Number.isFinite(to) && (allDay ? to > from : to >= from) ? [from, to] : null;
}

function possibleAllDayOverlap(record, from, to) {
  const start = civilDate(record.start?.date), end = civilDate(record.end?.date);
  // A date's civil midnight is within a day of that date at 00:00 UTC.
  // Valid but distant date-only records cannot affect this request. Missing
  // or malformed bounds cannot establish that exclusion and stay uncertain.
  return !start || !end || end <= start
    || overlapsBounds(start.getTime() - 86_400_000, end.getTime() + 86_400_000, from, to);
}

function selectCalendar(data, params, sync, now, staleAfterMs, ownerTimeZone) {
  if (data?.enabled !== true || data.coverage?.available === false || !Array.isArray(data.records)
      || data.recordCount !== undefined && (!Number.isSafeInteger(data.recordCount) || data.recordCount !== data.records.length)) {
    return {reply: unavailableCount("calendar", params?.window?.kind), matches: [], candidates: []};
  }
  const metadata = countMetadata(data, sync, now, staleAfterMs), resolver = civilResolver();
  const window = resolveCountWindow(params?.window, now, ownerTimeZone, resolver);
  const coverage = data.coverage;
  const zone = coverage?.calendarTimeZoneState === "consistent" ? canonicalTimeZone(coverage.calendarTimeZone) : null;
  const reply = { ...metadata, source: "calendar", scope: "configured-calendar-projection", filterSemantics: "sanitized-projected-fields",
    queryApplied: typeof params?.query === "string" && Boolean(params.query.trim()),
    observedMatchCount: null, completeMatchCount: null, requestCoveredByWindow: null,
    completeWithinWindow: false, unresolvedEventCount: 0, unresolvedAllDayEventCount: 0,
    ...Object.fromEntries(["windowKind", "windowState", "windowStart", "windowEnd", "windowDays", "ownerTimeZone"].map(key => [key, window[key]])),
    calendarTimeZone: zone };
  if (!zone) reply.limitations.push(coverage?.calendarTimeZoneState === "inconsistent"
    ? "calendar-time-zone-inconsistent" : "calendar-time-zone-unavailable");
  if (window.windowState !== "resolved") { reply.limitations.push(window.reason); return {reply, matches: [], candidates: []}; }
  const query = String(params.query ?? "").toLowerCase();
  const matches = [], candidates = [];
  const ids = new Set();
  for (const record of data.records) {
    if (!record || typeof record !== "object" || Array.isArray(record)
        || typeof record.id !== "string" || !record.id || record.id.length > 1024 || ids.has(record.id)
        || ["title", "notesSummary", "location"].some(key => (query || record[key] !== undefined) && typeof record[key] !== "string")) {
      reply.unresolvedEventCount += 1; continue;
    }
    ids.add(record.id);
    if (!matchesQuery(record, query)) continue;
    const bounds = strictEventBounds(record, zone, resolver);
    if (!bounds) {
      const allDay = record.start?.date !== undefined || record.end?.date !== undefined;
      if (allDay && !possibleAllDayOverlap(record, window.from, window.to)) continue;
      reply.unresolvedEventCount += 1;
      if (allDay) {
        reply.unresolvedAllDayEventCount += 1;
        candidates.push({record, unverified: true});
      }
      continue;
    }
    if (overlapsBounds(...bounds, window.from, window.to)) {
      matches.push(record);
      candidates.push({record, unverified: false});
    }
  }
  reply.observedMatchCount = matches.length;
  reply.requestCoveredByWindow = calendarWindowCovers(coverage, window.windowStart, window.windowEnd);
  const complete = coverage?.bounded === true && coverage.completeWithinWindow === true
    && coverage.truncated === false && coverage.sync?.state === "complete" && coverage.sync.retainedRecords === 0
    && coverage.sync.failure === null && !data.records.some(record => record?.retainedFromEarlierScan === true);
  if (!complete) reply.limitations.push(coverage?.completeWithinWindow === false || coverage?.truncated === true
    || ["limited", "in-progress"].includes(coverage?.sync?.state) ? "source-partial" : "coverage-unknown");
  if (reply.requestCoveredByWindow !== true) reply.limitations.push(reply.requestCoveredByWindow === false ? "window-outside-coverage" : "coverage-unknown");
  if (reply.unresolvedEventCount) reply.limitations.push("unresolved-event-time");
  reply.completeWithinWindow = complete && zone !== null && reply.unresolvedEventCount === 0
    && metadata.freshness === "fresh" && metadata.syncState === "ok" && reply.requestCoveredByWindow === true;
  if (reply.completeWithinWindow) reply.completeMatchCount = matches.length;
  reply.limitations = [...new Set(reply.limitations)];
  return {reply, matches, candidates};
}

export function countCalendar(data, params, sync, now, staleAfterMs, ownerTimeZone) {
  return selectCalendar(data, params, sync, now, staleAfterMs, ownerTimeZone).reply;
}
