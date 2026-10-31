import assert from "node:assert/strict";
import test from "node:test";
import { countEmail } from "../plugin/email-query.js";
import { countCalendar, resolveCountWindow } from "../plugin/calendar-query.js";

const NOW = Date.parse("2026-03-08T06:00:00Z"), TTL = 180000;
const at = (offset) => new Date(NOW + offset).toISOString();
const sync = { state: "ok" };
const mail = (id) => ({ id, threadId: "PRIVATE-THREAD", mailboxes: ["inbox"], labelIds: ["UNREAD"],
  subject: "PRIVATE-SUBJECT", from: "PRIVATE-SENDER", to: "PRIVATE-RECIPIENT", cc: "", summary: "PRIVATE-CONTENT" });
const email = (records = [mail("PRIVATE-ID")]) => ({ enabled: true, generatedAt: at(-1000), records,
  coverage: { bounded: true, sync: { state: "complete", retainedRecords: 0 },
    folders: Object.fromEntries(["inbox", "sent", "unread"].map(name => [name, { query: "PRIVATE-QUERY", completeWithinQuery: true, truncated: false }])) } });
const event = (start, end, id = "PRIVATE-EVENT") => ({ id, title: "PRIVATE-TITLE", location: "PRIVATE-LOCATION", notesSummary: "PRIVATE-NOTES", start, end });
const calendar = (records = [event({ dateTime: at(1000) }, { dateTime: at(2000) })]) => ({ enabled: true, generatedAt: at(-1000), records,
  coverage: { bounded: true, windowStart: at(-86400000 * 7), windowEnd: at(86400000 * 90), completeWithinWindow: true,
    truncated: false, calendarTimeZone: "Asia/Tokyo", calendarTimeZoneState: "consistent",
    sync: { state: "complete", retainedRecords: 0, failure: null } } });
const rolling = { window: { kind: "rolling", days: 7 } };
const count = (data, params = rolling, status = sync, now = NOW, owner = "America/New_York") => countCalendar(data, params, status, now, TTL, owner);

test("Gmail count emits no records/content/query and never a mailbox total", () => {
  const data = email(Array.from({ length: 13 }, (_, n) => mail(`PRIVATE-ID-${n}`)));
  const result = countEmail(data, "in:inbox is:unread", sync, NOW, TTL);
  assert.equal(result.indexedMatchCount, 13);
  assert.equal(result.recordsReturned, 0);
  assert.equal(result.completeWithinConfiguredQueries, true);
  assert.equal(result.mailboxTotal, null);
  assert.equal(result.canAnswerMailboxTotal, false);
  assert.doesNotMatch(JSON.stringify(result), /PRIVATE-|messages|threadId|subject|configuredQueries|in:inbox/);
});

test("Gmail fresh complete zero differs from partial, retained, stale, future and unknown zero", () => {
  const base = email([]);
  assert.equal(countEmail(base, "missing", sync, NOW, TTL).absenceWithinCoveredProjection, true);
  for (const mutate of [d => { d.coverage.sync.state = "in-progress"; },
    d => { d.coverage.sync.retainedRecords = 2; }, d => { d.generatedAt = at(-TTL - 1); },
    d => { d.generatedAt = at(1); }, d => { d.generatedAt = "2026-02-30T00:00:00Z"; }, d => { delete d.coverage; }]) {
    const data = structuredClone(base); mutate(data);
    const result = countEmail(data, "missing", sync, NOW, TTL);
    assert.equal(result.indexedMatchCount, 0);
    assert.equal(result.absenceWithinCoveredProjection, false);
  }
  const future = countEmail({ ...base, generatedAt: at(1) }, "", sync, NOW, TTL);
  assert.equal(future.freshness, "unknown"); assert.equal(future.ageSeconds, null);
  assert.equal(countEmail(base, "", null, NOW, TTL).completeWithinConfiguredQueries, false);
});

test("malformed or disabled Email is unavailable/null, never a successful zero", () => {
  for (const records of [null, [{}], [null], [mail("same"), mail("same")],
    [{ ...mail("x"), summary: { PRIVATE: true } }], [{ ...mail("x"), mailboxes: [] }]]) {
    const result = countEmail({ ...email(), records }, "missing", sync, NOW, TTL);
    assert.equal(result.readState, "unavailable"); assert.equal(result.indexedMatchCount, null);
    assert.equal(result.absenceWithinCoveredProjection, false);
  }
  assert.equal(countEmail({ ...email(), enabled: false }, "", sync, NOW, TTL).indexedMatchCount, null);
  assert.equal(countEmail({ ...email([]), recordCount: 2 }, "", sync, NOW, TTL).indexedMatchCount, null);
});

test("server-relative rolling window uses one given instant and no owner zone", () => {
  const result = resolveCountWindow({ kind: "rolling", days: 7 }, NOW, undefined);
  assert.equal(result.from, NOW); assert.equal(result.to - result.from, 7 * 86400000);
  assert.equal(result.ownerTimeZone, null); assert.equal(result.windowState, "resolved");
});

test("civil days use owner DST: 23/25 hours, not rolling 24h", () => {
  for (const [now, hours] of [["2026-03-08T12:00:00Z", 23], ["2026-11-01T12:00:00Z", 25]]) {
    const result = resolveCountWindow({ kind: "civil-days", start: "today", days: 1 }, Date.parse(now), "America/New_York");
    assert.equal(result.windowState, "resolved"); assert.equal(result.to - result.from, hours * 3600000);
  }
});

test("civil boundary and interior gaps, folds and skipped dates are unavailable", () => {
  for (const [now, zone, days] of [["2018-11-04T12:00:00Z", "America/Sao_Paulo", 1],
    ["2020-11-01T12:00:00Z", "America/Havana", 1], ["2011-12-29T12:00:00Z", "Pacific/Apia", 3],
    ["2020-10-31T12:00:00Z", "America/Havana", 3]]) {
    const result = resolveCountWindow({ kind: "civil-days", start: "today", days }, Date.parse(now), zone);
    assert.equal(result.windowState, "unavailable", `${now}/${zone}`);
    assert.equal(result.windowStart, null);
  }
  assert.equal(resolveCountWindow({ kind: "civil-days", start: "today", days: 1 }, NOW, undefined).windowState, "unavailable");
});

test("Calendar count is content-free and complete only within the covered fresh window", () => {
  const result = count(calendar());
  assert.equal(result.observedMatchCount, 1); assert.equal(result.completeMatchCount, 1);
  assert.equal(result.calendarTimeZone, "Asia/Tokyo"); assert.equal(result.ownerTimeZone, "America/New_York");
  assert.equal(result.recordsReturned, 0);
  assert.doesNotMatch(JSON.stringify(result), /PRIVATE-|events|notesSummary|title|location/);
  for (const mutate of [d => { d.coverage.completeWithinWindow = false; },
    d => { d.coverage.sync.retainedRecords = 1; }, d => { d.generatedAt = at(-TTL - 1); },
    d => { d.generatedAt = at(1); }, d => { delete d.coverage.calendarTimeZone; },
    d => { d.coverage.calendarTimeZoneState = "inconsistent"; }, d => { d.coverage.windowEnd = at(100); }]) {
    const data = calendar([]); mutate(data);
    assert.equal(count(data).completeMatchCount, null);
  }
  assert.equal(count(calendar([]), rolling, null).completeMatchCount, null);
  assert.equal(count({ ...calendar([]), recordCount: 2 }).observedMatchCount, null);
});

test("all-day boundaries use provider Calendar timezone, not owner/host timezone", () => {
  const data = calendar([event({ date: "2026-03-09" }, { date: "2026-03-10" })]);
  const result = count(data, { window: { kind: "absolute", timeMin: "2026-03-08T15:00:00Z", timeMax: "2026-03-08T16:00:00Z" } });
  assert.equal(result.completeMatchCount, 1); // Tokyo midnight, not New York midnight.
  data.coverage.calendarTimeZoneState = "unknown";
  const unknown = count(data);
  assert.equal(unknown.unresolvedEventCount, 1); assert.equal(unknown.completeMatchCount, null);
});

test("overlap includes ongoing events but excludes end==from and start==to", () => {
  const data = calendar([event({ dateTime: at(-2000) }, { dateTime: at(0) }, "ending"),
    event({ dateTime: at(-1000) }, { dateTime: at(1000) }, "ongoing"),
    event({ dateTime: at(2000) }, { dateTime: at(3000) }, "starting")]);
  assert.equal(count(data, { window: { kind: "absolute", timeMin: at(0), timeMax: at(2000) } }).completeMatchCount, 1);
});

test("unresolved Calendar bounds cannot certify zero even with nominal complete coverage", () => {
  for (const records of [[{}], [null], [event({ dateTime: "2026-02-30T00:00:00Z" }, { dateTime: at(1) })],
    [event({ dateTime: "2026-03-08T10:00:00" }, { dateTime: at(1) })]]) {
    const result = count(calendar(records));
    assert.equal(result.completeMatchCount, null); assert.equal(result.unresolvedEventCount, 1);
  }
});

test("missing or duplicated Calendar IDs and zero-duration all-day are unresolved", () => {
  const normal = event({ dateTime: at(1000) }, { dateTime: at(2000) });
  const missing = { ...normal }; delete missing.id;
  for (const records of [[missing], [normal, structuredClone(normal)],
    [event({ date: "2026-03-09" }, { date: "2026-03-09" })],
    [{ ...normal, title: { PRIVATE: true } }]]) {
    const result = count(calendar(records));
    assert.equal(result.completeMatchCount, null); assert.equal(result.unresolvedEventCount, 1);
  }
  const incomplete = { id: "event", start: normal.start, end: normal.end };
  assert.equal(count(calendar([incomplete]), { ...rolling, query: "missing" }).completeMatchCount, null);
});

test("unknown kind and invalid absolute bounds never invent an absolute resolved window", () => {
  assert.equal(resolveCountWindow({ kind: "PRIVATE-KIND" }, NOW, "UTC").windowKind, null);
  for (const timeMin of ["PRIVATE-TIME", "2026-02-30T00:00:00Z", "2026-03-08T06:00:00-00:00"]) {
    const result = count(calendar(), { window: { kind: "absolute", timeMin, timeMax: at(1000) } });
    assert.equal(result.windowState, "unavailable"); assert.equal(result.observedMatchCount, null);
    assert.doesNotMatch(JSON.stringify(result), /PRIVATE-/);
  }
});
