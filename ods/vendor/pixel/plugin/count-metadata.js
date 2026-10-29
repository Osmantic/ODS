// Count replies expose a fixed metadata vocabulary, never a spread of source data.
export const COUNT_KINDS = new Set(["absolute", "rolling", "civil-days"]);

export function canonicalTimestamp(value) {
  const match = typeof value === "string" && value.length <= 64
    && /^(\d{4}-\d{2}-\d{2})T(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d+)?)?(Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$/.exec(value);
  if (!match || match[2] === "-00:00") return null;
  const civil = new Date(`${match[1]}T00:00:00Z`);
  const time = Date.parse(value);
  return Number.isFinite(time) && Number.isFinite(civil.getTime()) && civil.toISOString().slice(0, 10) === match[1]
    ? new Date(time).toISOString() : null;
}

export function countMetadata(data, sync, now, staleAfterMs) {
  const generatedAt = canonicalTimestamp(data?.generatedAt);
  const generated = generatedAt === null ? NaN : Date.parse(generatedAt);
  const clockValid = Number.isFinite(now) && Number.isFinite(generated) && generated <= now;
  const ttlValid = Number.isFinite(staleAfterMs) && staleAfterMs >= 0;
  const freshness = !clockValid || !ttlValid ? "unknown" : now - generated > staleAfterMs ? "stale" : "fresh";
  const syncState = ["ok", "error", "disabled"].includes(sync?.state) ? sync.state : "unknown";
  const limitations = [];
  if (!clockValid) limitations.push("source-clock-invalid");
  if (!ttlValid) limitations.push("freshness-unverified");
  if (freshness === "stale") limitations.push("source-stale");
  if (syncState !== "ok") limitations.push("sync-unverified");
  return { schemaVersion: 1, recordsReturned: 0, generatedAt,
    ageSeconds: clockValid ? Math.max(0, Math.round((now - generated) / 1000)) : null,
    freshness, syncState, queryApplied: false, limitations, readState: "ok" };
}

export function unavailableCount(source, kind = null, reason = "source-unavailable") {
  const common = { schemaVersion: 1, recordsReturned: 0, generatedAt: null, ageSeconds: null,
    freshness: "unknown", syncState: "unknown", queryApplied: false,
    limitations: [reason], readState: "unavailable", source, filterSemantics: "sanitized-projected-fields" };
  return source === "gmail" ? { ...common, scope: "current-local-email-index", indexedMatchCount: null,
    mailboxTotal: null, canAnswerMailboxTotal: false, coverageState: "coverage-unknown",
    completeWithinConfiguredQueries: false, absenceWithinCoveredProjection: false }
    : { ...common, scope: "configured-calendar-projection", observedMatchCount: null, completeMatchCount: null,
      requestCoveredByWindow: null, completeWithinWindow: false, unresolvedEventCount: 0,
      windowKind: COUNT_KINDS.has(kind) ? kind : null, windowState: "unavailable",
      windowStart: null, windowEnd: null, windowDays: null, ownerTimeZone: null, calendarTimeZone: null };
}
