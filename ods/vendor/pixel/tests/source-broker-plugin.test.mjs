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
