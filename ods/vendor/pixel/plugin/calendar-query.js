// Calendar projection queries. Absence of events proves nothing outside the synced window.

function eventTime(record) {
  return Date.parse(record.start?.dateTime ?? record.start?.date ?? "");
}

export function calendarWindowCovers(coverage, timeMin, timeMax) {
  const bounds = [coverage?.windowStart, coverage?.windowEnd, timeMin, timeMax].map((value) => Date.parse(value ?? ""));
  if (!bounds.every(Number.isFinite)) return null;
  const [start, end, from, to] = bounds;
  return from >= start && to <= end;
}

export function listCalendar(data, params, limit) {
  const start = Date.parse(params.timeMin);
  const end = Date.parse(params.timeMax);
  const query = String(params.query ?? "").toLowerCase();
  const events = data.records.filter((item) => {
    const time = eventTime(item);
    return (!Number.isFinite(start) || time >= start) && (!Number.isFinite(end) || time <= end)
      && (!query || [item.title, item.notesSummary, item.location].join(" ").toLowerCase().includes(query));
  }).slice(0, limit);
  const coverage = data.coverage ?? null;
  return {
    generatedAt: data.generatedAt, ageSeconds: data.ageSeconds, stale: data.stale, count: events.length, events,
    coverage, requestCoveredByWindow: calendarWindowCovers(coverage, params.timeMin, params.timeMax),
  };
}
