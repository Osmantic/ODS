import assert from "node:assert/strict";
import { pageEmail, queryTerms, searchEmail } from "./email-query.js";

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
