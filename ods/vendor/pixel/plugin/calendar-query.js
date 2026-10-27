// Calendar projection queries. Absence of events proves nothing outside the synced window.

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
  return end > start ? start < to && end > from : start >= from && start < to;
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

export function listCalendar(data, params, limit) {
  const from = queryInstant(params.timeMin);
  const to = queryInstant(params.timeMax);
  for (const [key, value] of [["timeMin", from], ["timeMax", to]]) {
    if (!Number.isFinite(value)) throw new Error(`${key} must be a valid full ISO 8601 timestamp with date and Z or an explicit UTC offset. Read tool_describe before retrying.`);
  }
  if (to <= from) throw new Error("timeMax must be later than timeMin.");
  const query = String(params.query ?? "").toLowerCase();
  const matches = data.records.filter((item) => overlapsRequest(item, from, to)
    && (!query || [item.title, item.notesSummary, item.location].join(" ").toLowerCase().includes(query)));
  const events = matches.slice(0, limit).map(calendarEvent);
  const coverage = data.coverage ?? null;
  return {
    generatedAt: data.generatedAt, ageSeconds: data.ageSeconds, stale: data.stale,
    totalMatches: matches.length, count: events.length, hasMore: matches.length > events.length, events,
    coverage, requestCoveredByWindow: calendarWindowCovers(coverage, params.timeMin, params.timeMax),
  };
}
