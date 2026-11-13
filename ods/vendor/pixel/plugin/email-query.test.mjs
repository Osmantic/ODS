import assert from "node:assert/strict";
import test from "node:test";
import { emailCountScope, pageEmail, queryTerms, searchEmail } from "./email-query.js";

const records = [
  { id: "in-1", mailboxes: ["inbox"], labelIds: ["INBOX", "UNREAD"], from: "Lead <lead@example.invalid>", to: "operator@example.invalid", subject: "Workload inquiry", summary: "Needs a private AI deployment." },
  { id: "sent-1", mailboxes: ["sent"], labelIds: ["SENT"], from: "operator@example.invalid", to: "Lead <lead@example.invalid>", subject: "Re: Workload inquiry", summary: "" },
];

assert.deepEqual(queryTerms("in:sent to:lead@example.invalid").mailboxes, ["sent"]);
assert.deepEqual(searchEmail(records, "in:inbox is:unread").map(({ id }) => id), ["in-1"]);
assert.deepEqual(searchEmail(records, "in:sent to:lead@example.invalid").map(({ id }) => id), ["sent-1"]);
assert.deepEqual(searchEmail(records, "in:sent subject:workload").map(({ id }) => id), ["sent-1"]);
assert.deepEqual(searchEmail(records, "in:inbox in:sent"), []);
assert.deepEqual(pageEmail(records, "", 1, 1), {
  totalMatches: 2,
  offset: 1,
  limit: 1,
  count: 1,
  hasMore: false,
  messages: [records[1]],
});

function snapshot() {
  const folder = (query) => ({ query, completeWithinQuery: true, truncated: false, resultSizeEstimate: 999999 });
  return { stale: false, records: structuredClone(records), coverage: { bounded: true,
    folders: { inbox: folder("in:inbox newer_than:90d"), sent: folder("in:sent"),
      unread: folder("in:inbox newer_than:90d is:unread") }, sync: { state: "complete", retainedRecords: 0 } } };
}

test("a complete configured-query count never becomes a Gmail mailbox total", () => {
  const data = snapshot(), before = structuredClone(data);
  const counted = emailCountScope(data, "in:inbox", 1);
  assert.equal(counted.state, "complete-within-configured-queries");
  assert.equal(counted.freshness, "fresh");
  assert.equal(counted.mailboxTotal, null);
  assert.equal(counted.canAnswerMailboxTotal, false);
  assert.equal(counted.indexedMatches, 1); // Never the provider's estimate.
  assert.deepEqual(counted.configuredQueries, [{ folder: "inbox", query: "in:inbox newer_than:90d" }]);
  assert.match(counted.notice, /snapshot time/);
  assert.match(counted.notice, /projected fields only/);
  assert.deepEqual(data, before);
});

test("freshness must be explicitly false-stale before certifying a complete snapshot", () => {
  for (const stale of [undefined, null, 0, "false"]) {
    const data = snapshot();
    data.stale = stale;
    const counted = emailCountScope(data, "in:inbox", 1);
    assert.equal(counted.freshness, "unknown");
    assert.equal(counted.state, "coverage-unknown");
    assert.equal(counted.mailboxTotal, null);
  }
  const data = snapshot();
  data.stale = true;
  assert.equal(emailCountScope(data, "in:inbox", 1).state, "stale-index");
  assert.match(emailCountScope(data, "in:inbox", 1).notice, /historical scan; current synchronization is unverified/);
});

test("coverage and pruning require explicit proof, including a configured query", () => {
  for (const mutate of [
    (data) => { delete data.coverage; },
    (data) => { data.coverage.bounded = false; },
    (data) => { delete data.coverage.sync; },
    (data) => { delete data.coverage.sync.retainedRecords; },
    (data) => { data.coverage.sync.retainedRecords = false; },
    (data) => { delete data.coverage.folders.inbox; },
    (data) => { delete data.coverage.folders.inbox.query; },
    (data) => { data.coverage.folders.inbox.query = " "; },
    (data) => { delete data.coverage.folders.inbox.completeWithinQuery; },
    (data) => { delete data.coverage.folders.inbox.truncated; },
  ]) {
    const data = snapshot();
    mutate(data);
    assert.equal(emailCountScope(data, "in:inbox", 1).state, "coverage-unknown");
  }
});

test("a finished Inbox folder remains partial until global synchronization prunes earlier scans", () => {
  for (const state of ["in-progress", "limited"]) {
    const data = snapshot();
    data.coverage.sync.state = state;
    data.records[0].retainedFromEarlierScan = true;
    data.coverage.sync.retainedRecords = 1;
    const counted = emailCountScope(data, "in:inbox", 1);
    assert.equal(counted.state, "partial-index");
    assert.equal(counted.mailboxTotal, null);
    assert.match(counted.notice, /earlier-scan cache/);
  }
  const data = snapshot();
  data.coverage.sync.state = "in-progress";
  assert.equal(emailCountScope(data, "in:inbox", 1).state, "partial-index");
});

test("contradictory retained-cache or folder metadata cannot certify completeness", () => {
  for (const mutate of [
    (data) => { data.coverage.sync.retainedRecords = 1; },
    (data) => { data.records[0].retainedFromEarlierScan = true; },
    (data) => { data.coverage.folders.inbox.completeWithinQuery = false; },
    (data) => { data.coverage.folders.inbox.truncated = true; },
  ]) {
    const data = snapshot();
    mutate(data);
    assert.equal(emailCountScope(data, "in:inbox", 1).state, "partial-index");
    data.stale = true;
    assert.equal(emailCountScope(data, "in:inbox", 1).state, "stale-index");
  }
});

test("query scope follows the configured folders and unread projection without expanding it", () => {
  const data = snapshot();
  data.coverage.folders.unread.completeWithinQuery = false;
  assert.equal(emailCountScope(data, "in:inbox is:unread", 0).state, "partial-index");
  assert.equal(emailCountScope(data, "in:sent", 1).state, "complete-within-configured-queries");
  delete data.coverage.folders.sent;
  assert.equal(emailCountScope(data, "subject:unknown", 0).state, "coverage-unknown");
  assert.equal(emailCountScope(data, "in:inbox", 0).mailboxTotal, null);
});

test("pagination and empty pages retain their existing local-index meaning", () => {
  const data = snapshot();
  for (const [offset, count, hasMore] of [[0, 1, true], [1, 1, false], [999, 0, false]]) {
    const page = pageEmail(data.records, "", offset, 1);
    assert.equal(page.count, count);
    assert.equal(page.hasMore, hasMore);
    assert.equal(page.totalMatches, 2);
    const counted = emailCountScope(data, "", page.totalMatches);
    assert.equal(counted.state, "complete-within-configured-queries");
    assert.equal(counted.indexedMatches, 2);
    assert.equal(counted.mailboxTotal, null);
  }
  data.records = data.records.slice(0, 1);
  data.coverage.sync.state = "in-progress";
  const page = pageEmail(data.records, "in:inbox", 0, 1);
  assert.equal(page.hasMore, false);
  assert.equal(emailCountScope(data, "in:inbox", page.totalMatches).state, "partial-index");
});
