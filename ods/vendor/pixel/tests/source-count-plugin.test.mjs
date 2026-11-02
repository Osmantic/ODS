import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { register } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

register("./fixtures/source-plugin/loader.mjs", import.meta.url);
let sequence = 0;
async function fixture(t, files = {}, config = {}, flags = {}) {
  const path = await mkdtemp(join(tmpdir(), "source-count-private-"));
  t.after(() => rm(path, { recursive: true, force: true }));
  for (const [name, value] of Object.entries(files)) await writeFile(join(path, `${name}.json`), typeof value === "string" ? value : JSON.stringify(value));
  const keys = ["PIXEL_SOURCE_PROJECTION_DIR", "PIXEL_LIMB_WEB_ENABLED", "PIXEL_LIMB_EMAIL_ENABLED", "PIXEL_LIMB_CALENDAR_ENABLED"];
  const prior = Object.fromEntries(keys.map(key => [key, process.env[key]]));
  Object.assign(process.env, { PIXEL_SOURCE_PROJECTION_DIR: path, PIXEL_LIMB_WEB_ENABLED: "0", PIXEL_LIMB_EMAIL_ENABLED: "1", PIXEL_LIMB_CALENDAR_ENABLED: "1", ...flags });
  try {
    const { default: entry } = await import(`../plugin/index.js?count-test=${++sequence}`);
    const tools = new Map();
    entry.register({ ...config, registerTool(factory, { names }) { const tool = factory({ agentId: "pixel" }); if (tool) tools.set(names[0], tool); } });
    return tools;
  } finally {
    for (const [key, value] of Object.entries(prior)) if (value === undefined) delete process.env[key]; else process.env[key] = value;
  }
}
const boundary = { projectionOnly: true };
const generatedAt = new Date(Date.now() - 1000).toISOString();
const record = { id: "PRIVATE-ID", threadId: "PRIVATE-THREAD", subject: "PRIVATE-SUBJECT", from: "PRIVATE-SENDER",
  to: "PRIVATE-RECIPIENT", cc: "", summary: "PRIVATE-SUMMARY", labelIds: [], mailboxes: ["inbox"] };
const email = () => ({ schemaVersion: 1, sanitizerRevision: 3, enabled: true, generatedAt, boundary,
  records: [record], coverage: { bounded: true, sync: { state: "complete", retainedRecords: 0 },
    folders: Object.fromEntries(["inbox", "sent"].map(key => [key, { query: "PRIVATE-CONFIG-QUERY", completeWithinQuery: true, truncated: false }])) } });
const status = { kind: "pixel-source-broker-status", sources: { email: { state: "ok" }, calendar: { state: "ok" } } };
const common = ["schemaVersion", "recordsReturned", "generatedAt", "ageSeconds", "freshness", "syncState", "queryApplied",
  "limitations", "readState", "source", "scope", "filterSemantics"];
const keys = { pixel_gmail_count: [...common, "indexedMatchCount", "mailboxTotal", "canAnswerMailboxTotal", "coverageState",
  "completeWithinConfiguredQueries", "absenceWithinCoveredProjection"], pixel_calendar_count: [...common,
  "observedMatchCount", "completeMatchCount", "requestCoveredByWindow", "completeWithinWindow", "unresolvedEventCount",
  "windowKind", "windowState", "windowStart", "windowEnd", "windowDays", "ownerTimeZone", "calendarTimeZone"] };
function closed(name, response) {
  assert.deepEqual(Object.keys(response.details).sort(), [...keys[name]].sort());
  assert.equal(response.details.recordsReturned, 0);
  assert.doesNotMatch(JSON.stringify(response), /PRIVATE-|ENOENT|SyntaxError|\.json|node:fs|Unexpected token|\/tmp\//);
}

test("count parameters are refused by locked TypeBox before Source access and without input echo", async t => {
  const tools = await fixture(t);
  for (const [name, params] of [
    ["pixel_gmail_count", { maxResults: 0 }], ["pixel_gmail_count", { query: { PRIVATE: true } }],
    ["pixel_gmail_count", null], ["pixel_gmail_count", { query: "PRIVATE-QUERY", path: "PRIVATE-PATH" }],
    ["pixel_calendar_count", {}], ["pixel_calendar_count", { window: { kind: "PRIVATE-KIND" } }],
    ["pixel_calendar_count", { window: { kind: "rolling", days: "7" } }],
    ["pixel_calendar_count", { window: { kind: "rolling", days: 7, timeZone: "PRIVATE-ZONE" } }],
    ["pixel_calendar_count", { window: { kind: "civil-days", days: 0, start: "today" } }],
    ["pixel_calendar_count", { window: { kind: "rolling", days: 7 }, query: "PRIVATE-QUERY", now: "PRIVATE-TIME" }],
  ]) {
    const response = await tools.get(name).execute("private-schema-test", params);
    assert.equal(response.isError, true); closed(name, response);
    assert.deepEqual(response.details.limitations, ["invalid-parameters"]);
  }
});

test("valid count with missing/malformed/unavailable source is null with fixed errors, never zero", async t => {
  for (const files of [{}, { email: "{ PRIVATE-JSON", calendar: "{ PRIVATE-JSON" },
    { email: { ...email(), records: [{}] } }, { email: { ...email(), enabled: false } }]) {
    const tools = await fixture(t, files);
    for (const [name, args, field] of [["pixel_gmail_count", { query: "PRIVATE-QUERY" }, "indexedMatchCount"],
      ["pixel_calendar_count", { window: { kind: "rolling", days: 7 } }, "completeMatchCount"]]) {
      const response = await tools.get(name).execute("private-missing-test", args);
      closed(name, response); assert.equal(response.details.readState, "unavailable"); assert.equal(response.details[field], null);
      assert.equal(response.details.completeWithinConfiguredQueries ?? response.details.completeWithinWindow, false);
    }
  }
});

test("registered Gmail count preserves the complete catalog and returns no records", async t => {
  const tools = await fixture(t, { email: email(), status });
  const response = await tools.get("pixel_gmail_count").execute("private-valid-count", {});
  assert.equal(response.isError, undefined); closed("pixel_gmail_count", response);
  assert.equal(response.details.indexedMatchCount, 1); assert.equal(response.details.mailboxTotal, null);
  assert.equal(response.details.completeWithinConfiguredQueries, true);
  for (const name of ["pixel_gmail_inbox", "pixel_gmail_search", "pixel_gmail_read", "pixel_calendar_list", "pixel_calendar_get"]) assert.ok(tools.has(name));
});

test("turning off a source removes its count tool with its other readers", async t => {
  const tools = await fixture(t, {}, {}, { PIXEL_LIMB_EMAIL_ENABLED: "0", PIXEL_LIMB_CALENDAR_ENABLED: "0" });
  for (const name of ["pixel_gmail_count", "pixel_gmail_inbox", "pixel_gmail_read", "pixel_calendar_count", "pixel_calendar_list", "pixel_calendar_get"]) {
    assert.equal(tools.has(name), false);
  }
  assert.ok(tools.has("pixel_limb_status"));
});

test("count registered Calendar uses current owner timezone, separate from provider timezone", async t => {
  const calendar = { schemaVersion: 1, enabled: true, generatedAt, boundary, records: [],
    coverage: { bounded: true, windowStart: "2000-01-01T00:00:00Z", windowEnd: "2099-01-01T00:00:00Z",
      completeWithinWindow: true, truncated: false, calendarTimeZone: "Asia/Tokyo", calendarTimeZoneState: "consistent",
      sync: { state: "complete", retainedRecords: 0, failure: null } } };
  const tools = await fixture(t, { calendar, status }, { config: { agents: { defaults: { userTimezone: "UTC" } } },
    runtime: { config: { current: () => ({ agents: { defaults: { userTimezone: "America/New_York" } } }) } } });
  const response = await tools.get("pixel_calendar_count").execute("private-calendar-count", { window: { kind: "civil-days", days: 1, start: "tomorrow" } });
  closed("pixel_calendar_count", response); assert.equal(response.details.ownerTimeZone, "America/New_York");
  assert.equal(response.details.calendarTimeZone, "Asia/Tokyo"); assert.equal(response.details.completeMatchCount, 0);
});

test("missing owner zone is not replaced by host/configured provider zone for civil windows", async t => {
  const tools = await fixture(t, { calendar: { schemaVersion: 1, enabled: true, generatedAt, boundary, records: [],
    coverage: { calendarTimeZone: "UTC", calendarTimeZoneState: "consistent" } }, status });
  const response = await tools.get("pixel_calendar_count").execute("private-calendar-no-zone", { window: { kind: "civil-days", days: 1, start: "today" } });
  closed("pixel_calendar_count", response); assert.equal(response.details.ownerTimeZone, null);
  assert.equal(response.details.windowState, "unavailable"); assert.equal(response.details.observedMatchCount, null);
});
