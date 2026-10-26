// Calendar projection queries. Absence of events proves nothing outside the synced window.

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

export function calendarWindowCovers(coverage, timeMin, timeMax) {
  const bounds = [coverage?.windowStart, coverage?.windowEnd, timeMin, timeMax].map((value) => Date.parse(value ?? ""));
  if (!bounds.every(Number.isFinite)) return null;
  const [start, end, from, to] = bounds;
  return from >= start && to <= end;
}

export function listCalendar(data, params, limit) {
  const parsedFrom = Date.parse(params.timeMin);
  const parsedTo = Date.parse(params.timeMax);
  const from = Number.isFinite(parsedFrom) ? parsedFrom : -Infinity;
  const to = Number.isFinite(parsedTo) ? parsedTo : Infinity;
  const query = String(params.query ?? "").toLowerCase();
  const matches = data.records.filter((item) => overlapsRequest(item, from, to)
    && (!query || [item.title, item.notesSummary, item.location].join(" ").toLowerCase().includes(query)));
  const events = matches.slice(0, limit);
  const coverage = data.coverage ?? null;
  return {
    generatedAt: data.generatedAt, ageSeconds: data.ageSeconds, stale: data.stale,
    totalMatches: matches.length, count: events.length, hasMore: matches.length > events.length, events,
    coverage, requestCoveredByWindow: calendarWindowCovers(coverage, params.timeMin, params.timeMax),
  };
}
