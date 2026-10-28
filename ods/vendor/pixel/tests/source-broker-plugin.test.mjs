import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { register } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { calendarWindowCovers } from "../plugin/calendar-query.js";

register("./fixtures/source-plugin/loader.mjs", import.meta.url);

const PROPOSE_TOOLS = ["pixel_calendar_propose_create", "pixel_calendar_propose_update", "pixel_calendar_propose_delete"];
const LIMB_KEYS = ["PIXEL_LIMB_EMAIL_ENABLED", "PIXEL_LIMB_CALENDAR_ENABLED", "PIXEL_LIMB_CALENDAR_WRITE_ENABLED",
  "PIXEL_LIMB_SOCIAL_ENABLED", "PIXEL_LIMB_WEB_ENABLED", "PIXEL_SOURCE_PROJECTION_DIR",
  "PIXEL_LIMB_EMAIL_DRAFTS_ENABLED", "PIXEL_ACTION_PROPOSAL_DIR"];
let instance = 0;

// The plugin reads its limb flags once at load, so each case loads a fresh module instance.
async function loadPlugin(env) {
  const saved = Object.fromEntries(LIMB_KEYS.map((key) => [key, process.env[key]]));
  for (const key of LIMB_KEYS) delete process.env[key];
  Object.assign(process.env, env);
  try {
    instance += 1;
    const { default: entry } = await import(`../plugin/index.js?case=${instance}`);
    const tools = new Map();
    entry.register({
      registerTool(factory, { names }) {
        const tool = factory({ agentId: "pixel" });
        if (tool) tools.set(names[0], tool);
      },
    });
    return tools;
  } finally {
    for (const [key, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
}

async function call(tools, name, params = {}) {
  const value = await tools.get(name).execute("call-1", params);
  assert.equal(value.isError, undefined, value.content?.[0]?.text);
  return value.details;
}

async function withProjections(t, files) {
  const directory = await mkdtemp(join(tmpdir(), "pixel-source-plugin-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  for (const [name, value] of Object.entries(files)) {
    await writeFile(join(directory, `${name}.json`), `${JSON.stringify(value)}\n`);
  }
  return directory;
}

const boundary = { rawContentStored: false, instructionsAuthorized: false, projectionOnly: true };
const now = Date.now();
const iso = (offsetDays) => new Date(now + offsetDays * 86_400_000).toISOString();

function calendarProjection(overrides = {}) {
  return {
    schemaVersion: 1, source: "google-calendar", enabled: true, generatedAt: iso(0), startedAt: iso(0),
    recordCount: 1, sanitizerRevision: 2, boundary,
    records: [{ id: "event-1", title: "Dentist", start: { dateTime: iso(2) }, end: { dateTime: iso(2.02) } }],
    coverage: { bounded: true, windowStart: iso(-7), windowEnd: iso(90), completeWithinWindow: true, truncated: false },
    ...overrides,
  };
}

const status = {
  schemaVersion: 1, kind: "pixel-source-broker-status", startedAt: iso(0), generatedAt: iso(0), grantedScopes: [],
  boundary: { contentFree: true, credentialsIncluded: false },
  sources: {
    email: { enabled: true, state: "error", errorClass: "api-disabled", httpStatus: 403, errorType: "UpstreamHTTPError",
      lastAttemptAt: iso(0), lastSuccessAt: null, failingSince: iso(0), recordCount: 0, complete: false },
    calendar: { enabled: true, state: "ok", errorClass: null, httpStatus: null, errorType: null,
      lastAttemptAt: iso(0), lastSuccessAt: iso(0), failingSince: null, recordCount: 1, complete: true },
  },
};

test("Calendar change tools register only while Calendar changes are on", async () => {
  const unchanged = await loadPlugin({ PIXEL_LIMB_WEB_ENABLED: "0" });
  for (const name of PROPOSE_TOOLS) assert.ok(unchanged.has(name), `${name} is registered by default`);
  const readOnly = await loadPlugin({ PIXEL_LIMB_WEB_ENABLED: "0", PIXEL_LIMB_CALENDAR_WRITE_ENABLED: "0" });
  for (const name of PROPOSE_TOOLS) assert.equal(readOnly.has(name), false, `${name} is absent when writes are off`);
  assert.ok(readOnly.has("pixel_calendar_list") && readOnly.has("pixel_calendar_get"));
  const limbs = (await call(readOnly, "pixel_limb_status")).limbs;
  assert.equal(limbs.calendar, true);
  assert.equal(limbs.calendarWrite, false);
});

test("limb status reports the web limb the gateway was configured with", async () => {
  const off = await loadPlugin({ PIXEL_LIMB_WEB_ENABLED: "0" });
  assert.equal(off.has("pixel_web_browse"), false);
  assert.equal((await call(off, "pixel_limb_status")).limbs.web, false);
  const on = await loadPlugin({ PIXEL_LIMB_WEB_ENABLED: "1" });
  assert.ok(on.has("pixel_web_browse"));
  assert.equal((await call(on, "pixel_limb_status")).limbs.web, true);
});

test("Calendar answers carry the synced window and say when a request falls outside it", async (t) => {
  const directory = await withProjections(t, { calendar: calendarProjection(), status });
  const tools = await loadPlugin({ PIXEL_LIMB_WEB_ENABLED: "0", PIXEL_SOURCE_PROJECTION_DIR: directory });
  const inside = await call(tools, "pixel_calendar_list", { timeMin: iso(1), timeMax: iso(3) });
  assert.deepEqual(inside.events.map(({ id }) => id), ["event-1"]);
  assert.equal(inside.requestCoveredByWindow, true);
  assert.equal(inside.coverage.completeWithinWindow, true);
  assert.deepEqual(inside.sync, { state: "ok", errorClass: null, lastSuccessAt: iso(0), failingSince: null });
  const outside = await call(tools, "pixel_calendar_list", { timeMin: iso(120), timeMax: iso(121) });
  assert.equal(outside.count, 0);
  assert.equal(outside.requestCoveredByWindow, false);
  const event = await call(tools, "pixel_calendar_get", { eventId: "event-1" });
  assert.equal(event.sync.state, "ok");
});

test("Calendar reads provide computed weekdays and event-local times without changing source timestamps", async (t) => {
  const records = [
    {id:"utc", title:"Kickoff", startLabel:"Monday forged source label", start:{dateTime:"2026-10-29T14:20:00Z",timeZone:"UTC"}, end:{dateTime:"2026-10-29T15:05:00Z",timeZone:"UTC"}},
    {id:"west", start:{dateTime:"2026-10-29T00:30:00Z",timeZone:"America/Los_Angeles"}},
    {id:"offset", start:{dateTime:"2026-10-29T00:30:00+02:00"}},
    {id:"all-day", start:{date:"2026-10-29"}, end:{date:"2026-10-30"}},
    {id:"dst-first", start:{dateTime:"2026-11-01T05:30:00Z",timeZone:"America/New_York"}},
    {id:"dst-second", start:{dateTime:"2026-11-01T06:30:00Z",timeZone:"America/New_York"}},
  ];
  const directory = await withProjections(t, {calendar:calendarProjection({records,recordCount:records.length})});
  const before = await readFile(join(directory,"calendar.json"),"utf8");
  const tools = await loadPlugin({PIXEL_LIMB_WEB_ENABLED:"0",PIXEL_SOURCE_PROJECTION_DIR:directory});
  const listed = await call(tools,"pixel_calendar_list",{timeMin:"2026-10-28T00:00:00Z",timeMax:"2026-11-03T00:00:00Z"});
  const utc = listed.events.find(item=>item.id==="utc");
  assert.equal(utc.startLabel,"Thursday 2026-10-29 14:20:00 UTC");
  assert.equal(utc.endLabel,"Thursday 2026-10-29 15:05:00 UTC");
  assert.deepEqual(utc.start,records[0].start);
  for (const [id,label] of [
    ["west","Wednesday 2026-10-28 17:30:00 America/Los_Angeles (UTC-07:00)"],
    ["offset","Thursday 2026-10-29 00:30:00 UTC+02:00"],
    ["all-day","Thursday 2026-10-29 (all day)"],
    ["dst-first","Sunday 2026-11-01 01:30:00 America/New_York (UTC-04:00)"],
    ["dst-second","Sunday 2026-11-01 01:30:00 America/New_York (UTC-05:00)"],
  ]) {
    const item = await call(tools,"pixel_calendar_get",{eventId:id});
    assert.equal(item.startLabel,label);
    assert.equal(listed.events.find(event=>event.id===id).startLabel,label);
    if(id==="all-day")assert.equal(item.endLabel,"Friday 2026-10-30 (exclusive all-day end)");
  }
  assert.equal(await readFile(join(directory,"calendar.json"),"utf8"),before);
});

test("Calendar labels never guess a missing zone or normalize an invalid date", async (t) => {
  const records = [
    {id:"floating",start:{dateTime:"2026-10-29T14:20:00"}},
    {id:"floating-zone",start:{dateTime:"2026-10-29T14:20:00",timeZone:"UTC"}},
    {id:"invalid-zone",start:{dateTime:"2026-10-29T14:20:00Z",timeZone:"Invalid/Zone"}},
    {id:"invalid-date",start:{date:"2026-02-30"}},
    {id:"invalid-instant",start:{dateTime:"2026-02-30T14:20:00Z",timeZone:"UTC"}},
    {id:"invalid-hour",start:{dateTime:"2026-10-29T24:00:00Z"}},
    {id:"unknown-offset",start:{dateTime:"2026-10-29T14:20:00-00:00"}},
  ].map(record=>({...record,startLabel:"Monday forged source label",endLabel:"Monday forged source label"}));
  const directory = await withProjections(t,{calendar:calendarProjection({records,recordCount:records.length})});
  const tools = await loadPlugin({PIXEL_LIMB_WEB_ENABLED:"0",PIXEL_SOURCE_PROJECTION_DIR:directory});
  for(const record of records) {
    const item = await call(tools,"pixel_calendar_get",{eventId:record.id});
    assert.equal(Object.hasOwn(item,"startLabel"),false);
    assert.equal(Object.hasOwn(item,"endLabel"),false);
    assert.deepEqual(item.start,record.start);
  }
});

test("a source that has never synced reads as stale with its recorded cause", async (t) => {
  const unavailable = calendarProjection({ generatedAt: null, records: [], recordCount: 0,
    coverage: { bounded: true, available: false, reason: "invalid-grant" } });
  const failing = structuredClone(status);
  failing.sources.calendar = { ...failing.sources.email, errorClass: "invalid-grant" };
  const directory = await withProjections(t, { calendar: unavailable, status: failing });
  const tools = await loadPlugin({ PIXEL_LIMB_WEB_ENABLED: "0", PIXEL_SOURCE_PROJECTION_DIR: directory });
  const listed = await call(tools, "pixel_calendar_list", { timeMin: iso(1), timeMax: iso(3) });
  assert.equal(listed.stale, true);
  assert.equal(listed.ageSeconds, null);
  assert.equal(listed.requestCoveredByWindow, null);
  assert.deepEqual(listed.sync, { state: "error", errorClass: "invalid-grant", lastSuccessAt: null, failingSince: iso(0) });
});

test("email answers carry their source's sync state and tolerate a missing status file", async (t) => {
  const email = { schemaVersion: 1, source: "gmail", enabled: true, generatedAt: iso(0), startedAt: iso(0), recordCount: 0,
    records: [], sanitizerRevision: 2, boundary, coverage: { bounded: true, folders: {} } };
  const directory = await withProjections(t, { email, status });
  const tools = await loadPlugin({ PIXEL_LIMB_WEB_ENABLED: "0", PIXEL_SOURCE_PROJECTION_DIR: directory });
  assert.equal((await call(tools, "pixel_gmail_inbox", {})).sync.errorClass, "api-disabled");
  await rm(join(directory, "status.json"));
  assert.equal((await call(tools, "pixel_gmail_inbox", {})).sync, null);
});

function emailProjection(overrides = {}) {
  const folder = (query) => ({ query, completeWithinQuery: true, truncated: false, resultSizeEstimate: 999999 });
  return { schemaVersion: 1, source: "gmail", enabled: true, generatedAt: iso(0), startedAt: iso(0),
    sanitizerRevision: 2, boundary, recordCount: 3,
    records: [
      { id: "in-a", threadId: "thread-a", mailboxes: ["inbox"], labelIds: ["INBOX"], summary: "Synthetic first summary.", retainedFromEarlierScan: false },
      { id: "in-b", threadId: "thread-a", mailboxes: ["inbox"], labelIds: ["INBOX"], summary: "Synthetic second summary.", retainedFromEarlierScan: false },
      { id: "sent-a", threadId: "thread-a", mailboxes: ["sent"], labelIds: ["SENT"], contentProjection: "metadata-only", retainedFromEarlierScan: false },
    ], coverage: { bounded: true, folders: { inbox: folder("in:inbox newer_than:90d"), sent: folder("in:sent"),
      unread: folder("in:inbox newer_than:90d is:unread") }, sync: { state: "complete", retainedRecords: 0 } },
    ...overrides };
}

test("Gmail pages frontload unknown mailbox totals even after exhausting the current partial index", async (t) => {
  const email = emailProjection();
  email.coverage.sync = { state: "in-progress", retainedRecords: 1 };
  email.coverage.folders.sent.completeWithinQuery = false;
  email.records[0].retainedFromEarlierScan = true;
  const directory = await withProjections(t, { email });
  const before = await readFile(join(directory, "email.json"), "utf8");
  const tools = await loadPlugin({ PIXEL_LIMB_WEB_ENABLED: "0", PIXEL_SOURCE_PROJECTION_DIR: directory });
  for (const [name, params, matches] of [
    ["pixel_gmail_inbox", {}, 2], ["pixel_gmail_search", { query: "in:inbox" }, 2],
    ["pixel_gmail_sent", {}, 1], ["pixel_gmail_thread", { threadId: "thread-a" }, 3],
  ]) {
    const response = await tools.get(name).execute("count-fixture", { ...params, maxResults: 1 });
    assert.equal(response.isError, undefined);
    const result = response.details;
    assert.equal(Object.keys(result)[0], "counting");
    assert.equal(result.count, 1);
    assert.equal(result.hasMore, matches > 1);
    assert.equal(result.totalMatches, matches);
    assert.equal(result.counting.indexedMatches, matches);
    assert.equal(result.counting.mailboxTotal, null);
    assert.equal(result.counting.canAnswerMailboxTotal, false);
    assert.equal(result.counting.state, "partial-index");
    assert.equal(result.counting.freshness, "fresh");
    assert.ok(response.content[0].text.indexOf('"mailboxTotal"') < response.content[0].text.indexOf('"totalMatches"'));
  }
  const last = await call(tools, "pixel_gmail_inbox", { maxResults: 1, offset: 1 });
  assert.equal(last.count, 1);
  assert.equal(last.totalMatches, 2);
  assert.equal(last.hasMore, false);
  assert.equal(last.counting.state, "partial-index");
  assert.equal(last.counting.mailboxTotal, null);
  assert.equal(await readFile(join(directory, "email.json"), "utf8"), before);
});

test("fresh complete Gmail results describe only their configured query snapshot", async (t) => {
  const directory = await withProjections(t, { email: emailProjection() });
  const tools = await loadPlugin({ PIXEL_LIMB_WEB_ENABLED: "0", PIXEL_SOURCE_PROJECTION_DIR: directory });
  const first = await call(tools, "pixel_gmail_inbox", { maxResults: 1 });
  assert.equal(first.hasMore, true);
  assert.equal(first.counting.state, "complete-within-configured-queries");
  assert.match(first.counting.notice, /hasMore means more pages.*not incomplete synchronization/);
  const result = await call(tools, "pixel_gmail_inbox", { maxResults: 1, offset: 100 });
  assert.equal(result.count, 0);
  assert.equal(result.totalMatches, 2);
  assert.equal(result.counting.state, "complete-within-configured-queries");
  assert.deepEqual(result.counting.configuredQueries, [{ folder: "inbox", query: "in:inbox newer_than:90d" }]);
  assert.equal(result.counting.mailboxTotal, null);
  assert.match(result.counting.notice, /projected fields only/);
});

test("stale or unqualified Gmail snapshots cannot certify current absence", async (t) => {
  for (const [email, expected] of [
    [emailProjection({ generatedAt: iso(-1) }), "stale-index"],
    [emailProjection({ coverage: { bounded: true, folders: {} } }), "coverage-unknown"],
  ]) {
    const directory = await withProjections(t, { email });
    const tools = await loadPlugin({ PIXEL_LIMB_WEB_ENABLED: "0", PIXEL_SOURCE_PROJECTION_DIR: directory });
    const result = await call(tools, "pixel_gmail_search", { query: "subject:missing" });
    assert.equal(result.count, 0);
    assert.equal(result.counting.state, expected);
    assert.equal(result.counting.mailboxTotal, null);
    assert.match(result.counting.notice, /current absence is not established/);
  }
});

test("a missing source argument gives a repairable error before projection access", async (t) => {
  const directory = await withProjections(t, {}); // No projection exists to read.
  const tools = await loadPlugin({ PIXEL_LIMB_WEB_ENABLED: "0", PIXEL_SOURCE_PROJECTION_DIR: directory });
  for (const [name, params, field] of [
    ["pixel_gmail_read", { id: "private-message-value" }, "messageId"],
    ["pixel_gmail_search", {}, "query"],
    ["pixel_gmail_thread", null, "threadId"],
    ["pixel_calendar_get", {}, "eventId"],
    ["pixel_calendar_list", { timeMin: iso(1) }, "timeMax"],
  ]) {
    const response = await tools.get(name).execute("bad-call", params);
    assert.equal(response.isError, true, name);
    assert.match(response.content[0].text, new RegExp(`Missing required parameter\\(s\\): ${field}`));
    assert.ok(response.content[0].text.includes(`tool_describe for ${name}`));
    assert.doesNotMatch(response.content[0].text, /private-message-value|ENOENT|projection record not found/);
  }
});

test("after a malformed Gmail read, the declared messageId reads the actual record", async (t) => {
  const email = { schemaVersion: 1, generatedAt: iso(0), boundary,
    records: [{ id: "email-1", summary: "Kickoff Thursday at 14:20. Confirmation JADE-TEST." }] };
  const directory = await withProjections(t, { email });
  const tools = await loadPlugin({ PIXEL_LIMB_WEB_ENABLED: "0", PIXEL_SOURCE_PROJECTION_DIR: directory });
  const refused = await tools.get("pixel_gmail_read").execute("bad-call", { id: "email-1" });
  assert.equal(refused.isError, true);
  assert.match(refused.content[0].text, /Missing required parameter\(s\): messageId/);
  const read = await call(tools, "pixel_gmail_read", { messageId: "email-1" });
  assert.equal(read.id, "email-1");
  assert.equal(read.summary, email.records[0].summary);
});

test("missing proposal fields never create a partial draft or calendar proposal", async (t) => {
  const directory = await withProjections(t, {});
  const tools = await loadPlugin({ PIXEL_LIMB_WEB_ENABLED: "0", PIXEL_LIMB_EMAIL_DRAFTS_ENABLED: "1",
    PIXEL_ACTION_PROPOSAL_DIR: directory });
  for (const [name, params, field] of [
    ["pixel_gmail_draft_propose", { to: [{ email: "pat@example.com" }] }, "body"],
    ["pixel_calendar_propose_delete", { eventId: "event-1" }, "expectedEtag"],
  ]) {
    const response = await tools.get(name).execute("bad-proposal", params);
    assert.equal(response.isError, true);
    assert.ok(response.content[0].text.includes(`Missing required parameter(s): ${field}`));
  }
  assert.deepEqual(await readdir(directory), []);
});

test("a meeting already running or an all-day event counts as busy", async (t) => {
  const at = (hours) => new Date(Date.parse("2026-11-02T00:00:00Z") + hours * 3_600_000).toISOString();
  const records = [
    { id: "running", title: "Design review", start: { dateTime: at(9) }, end: { dateTime: at(11) } },
    { id: "trip", title: "Conference", start: { date: "2026-11-01" }, end: { date: "2026-11-03" } },
    { id: "ended", title: "Standup", start: { dateTime: at(8) }, end: { dateTime: at(10) } },
    { id: "later", title: "Lunch", start: { dateTime: at(12) }, end: { dateTime: at(13) } },
  ];
  const directory = await withProjections(t, { calendar: calendarProjection({ records, recordCount: records.length }) });
  const tools = await loadPlugin({ PIXEL_LIMB_WEB_ENABLED: "0", PIXEL_SOURCE_PROJECTION_DIR: directory });
  const busy = await call(tools, "pixel_calendar_list", { timeMin: at(10), timeMax: at(10.5) });
  assert.deepEqual(busy.events.map(({ id }) => id).sort(), ["running", "trip"]);
  assert.equal(busy.totalMatches, 2);
  const limited = await call(tools, "pixel_calendar_list", { timeMin: at(0), timeMax: at(24), maxResults: 1 });
  assert.equal(limited.count, 1);
  assert.equal(limited.totalMatches, 4);
  assert.equal(limited.hasMore, true);
});

test("window coverage needs both bounds and a valid request", () => {
  const coverage = { windowStart: iso(-7), windowEnd: iso(90) };
  assert.equal(calendarWindowCovers(coverage, iso(-7), iso(90)), true);
  assert.equal(calendarWindowCovers(coverage, iso(-8), iso(1)), false);
  assert.equal(calendarWindowCovers(coverage, "not a time", iso(1)), null);
  assert.equal(calendarWindowCovers(null, iso(1), iso(2)), null);
});

test("Calendar refuses ambiguous or malformed intervals instead of silently widening the search", async (t) => {
  const projection = calendarProjection({records:[{id:"precise-event",title:"Private appointment",
    start:{dateTime:"2026-10-29T01:00:00Z"},end:{dateTime:"2026-10-29T01:30:00Z"}}]});
  const directory = await withProjections(t,{calendar:projection});
  const tools = await loadPlugin({PIXEL_LIMB_WEB_ENABLED:"0",PIXEL_SOURCE_PROJECTION_DIR:directory});
  const before = await readFile(join(directory,"calendar.json"));
  const valid = {timeMin:"2026-10-29T00:00:00Z",timeMax:"2026-10-29T02:00:00Z"};
  for (const bad of ["2026-10-29T00:00", "2026-10-29", "not-a-valid-time", "2026-02-30T00:00:00Z",
    "2026-10-29T24:00:00Z", "2026-10-29T00:00:00+24:00", "2026-10-29T00:00:00-00:00",null]) {
    for (const key of ["timeMin","timeMax"]) {
      const response = await tools.get("pixel_calendar_list").execute("bad-interval",{...valid,[key]:bad});
      assert.equal(response.isError,true,`${key}: ${bad}`);
      assert.match(response.content[0].text,new RegExp(key));
      assert.ok(!JSON.stringify(response).includes("Private appointment"));
    }
  }
  for (const timeMax of [valid.timeMin,"2026-10-28T23:59:00Z"]) {
    const response = await tools.get("pixel_calendar_list").execute("reversed-interval",{...valid,timeMax});
    assert.equal(response.isError,true);
    assert.match(response.content[0].text,/timeMax.*later than timeMin/);
  }
  assert.deepEqual(await readFile(join(directory,"calendar.json")),before);
});

test("Calendar queries compare explicit instants consistently across offsets and precision", async (t) => {
  const directory = await withProjections(t,{calendar:calendarProjection({records:[{id:"offset-event",title:"Review",
    start:{dateTime:"2026-10-29T01:00:00Z"},end:{dateTime:"2026-10-29T01:30:00Z"}}],
    coverage:{windowStart:"2026-10-28T00:00:00Z",windowEnd:"2026-10-30T00:00:00Z",completeWithinWindow:true}})});
  const tools = await loadPlugin({PIXEL_LIMB_WEB_ENABLED:"0",PIXEL_SOURCE_PROJECTION_DIR:directory});
  for (const [timeMin,timeMax] of [
    ["2026-10-29T00:00:00Z","2026-10-29T02:00:00Z"],
    ["2026-10-28T21:00:00-03:00","2026-10-28T23:00:00-03:00"],
    ["2026-10-29T09:00:00+09:00","2026-10-29T11:00:00+09:00"],
    ["2026-10-29T00:00Z","2026-10-29T02:00Z"],
    ["2026-10-29T00:00:00.000000Z","2026-10-29T02:00:00.000Z"],
  ]) {
    const response = await call(tools,"pixel_calendar_list",{timeMin,timeMax});
    assert.deepEqual(response.events.map(({id})=>id),["offset-event"]);
    assert.equal(response.requestCoveredByWindow,true);
  }
  const coverage = {windowStart:"2026-10-28T00:00:00Z",windowEnd:"2026-10-30T00:00:00Z"};
  assert.equal(calendarWindowCovers(coverage,"2026-10-29T00:00","2026-10-29T02:00"),null);
  assert.equal(calendarWindowCovers(coverage,"2026-10-29T02:00Z","2026-10-29T00:00Z"),null);
});

test("the Gmail draft tool registers only while drafts are on, and only proposes", async (t) => {
  const directory = await mkdtemp(join(tmpdir(), "pixel-draft-proposals-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const off = await loadPlugin({ PIXEL_LIMB_WEB_ENABLED: "0" });
  assert.equal(off.has("pixel_gmail_draft_propose"), false, "drafts are off by default");
  assert.equal((await call(off, "pixel_limb_status")).limbs.emailDrafts, false);
  const noEmail = await loadPlugin({ PIXEL_LIMB_WEB_ENABLED: "0", PIXEL_LIMB_EMAIL_ENABLED: "0", PIXEL_LIMB_EMAIL_DRAFTS_ENABLED: "1" });
  assert.equal(noEmail.has("pixel_gmail_draft_propose"), false, "drafts need the email limb");
  const on = await loadPlugin({ PIXEL_LIMB_WEB_ENABLED: "0", PIXEL_LIMB_EMAIL_DRAFTS_ENABLED: "1", PIXEL_ACTION_PROPOSAL_DIR: directory });
  assert.equal((await call(on, "pixel_limb_status")).limbs.emailDrafts, true);
  const draft = await call(on, "pixel_gmail_draft_propose", { to: [{ email: "pat@example.com" }], subject: "Lunch", body: "Friday at noon?" });
  assert.match(draft.proposalId, /^gmail-draft-[0-9]{13}-[a-f0-9]{8}$/);
  assert.equal(draft.status, "pending-operator-approval");
  assert.equal(draft.action, "create-draft");
  assert.match(draft.operatorInstruction, /never sends email/);
  const written = JSON.parse(await readFile(join(directory, `${draft.proposalId}.json`), "utf8"));
  assert.deepEqual(written.values, { to: [{ email: "pat@example.com" }], subject: "Lunch", body: "Friday at noon?" });
  assert.equal(written.boundary, "This file is a proposal only. No Gmail draft has been created.");
  const refused = await on.get("pixel_gmail_draft_propose").execute("call-2", { to: [{ email: "pat@example.com" }], body: "[quarantined email body]" });
  assert.equal(refused.isError, true);
});
