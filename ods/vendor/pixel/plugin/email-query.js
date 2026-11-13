import { countMetadata, unavailableCount } from "./count-metadata.js";

export function queryTerms(query) {
  const fields = {};
  let remaining = String(query ?? "");
  for (const field of ["subject", "from", "to", "cc"]) {
    const pattern = new RegExp(`${field}:\\s*(?:"([^"]*)"|(\\S+))`, "ig");
    remaining = remaining.replace(pattern, (_match, quoted, plain) => {
      fields[field] = (quoted ?? plain ?? "").toLowerCase();
      return " ";
    });
  }
  const mailboxes = [...remaining.matchAll(/(?:^|\s)in:(inbox|sent)(?=\s|$)/ig)]
    .map((match) => match[1].toLowerCase());
  const unread = /(?:^|\s)is:unread(?:\s|$)/i.test(remaining);
  remaining = remaining.replace(/(?:^|\s)(?:is:unread|in:(?:inbox|sent))(?=\s|$)/ig, " ");
  const terms = remaining.match(/"([^"]+)"|\S+/g)?.map((item) => item.replace(/^"|"$/g, "").toLowerCase()) ?? [];
  return { fields, mailboxes: [...new Set(mailboxes)], unread, terms };
}

export function searchEmail(records, query) {
  const { fields, mailboxes, unread, terms } = queryTerms(query);
  return records.filter((record) => {
    if (mailboxes.some((mailbox) => !(record.mailboxes ?? []).includes(mailbox))) return false;
    if (unread && !(record.labelIds ?? []).includes("UNREAD")) return false;
    for (const [field, expected] of Object.entries(fields)) {
      if (!String(record[field] ?? "").toLowerCase().includes(expected)) return false;
    }
    const haystack = [record.subject, record.from, record.to, record.cc, record.summary].join(" ").toLowerCase();
    return terms.every((term) => haystack.includes(term));
  });
}

export function pageEmail(records, query, offset = 0, limit = 50) {
  const matches = searchEmail(records, query);
  const safeOffset = Math.min(Math.max(Number(offset) || 0, 0), 100000);
  const safeLimit = Math.min(Math.max(Number(limit) || 50, 1), 100);
  const messages = matches.slice(safeOffset, safeOffset + safeLimit);
  return {
    totalMatches: matches.length,
    offset: safeOffset,
    limit: safeLimit,
    count: messages.length,
    hasMore: safeOffset + messages.length < matches.length,
    messages,
  };
}

// A page counts matches in the local projection, not every message in Gmail.
// Even a complete scan covers the broker's configured folder queries only.
export function emailCountScope(data, query, indexedMatches) {
  const { mailboxes: requested, unread } = queryTerms(query);
  const required = requested.length ? requested : ["inbox", "sent"];
  if (unread && required.includes("inbox")) required.push("unread");
  const coverage = data?.coverage;
  const folders = required.map((name) => coverage?.folders?.[name]);
  const retained = (Number.isSafeInteger(coverage?.sync?.retainedRecords) && coverage.sync.retainedRecords > 0)
    || (Array.isArray(data?.records) && data.records.some((record) => record?.retainedFromEarlierScan === true));
  const freshness = data?.stale === false ? "fresh" : data?.stale === true ? "stale" : "unknown";
  // A finished folder still shares the projection's retained cache until the
  // entire scan completes/prunes. Its flag alone cannot certify these counts.
  const complete = freshness === "fresh" && coverage?.bounded === true && coverage?.sync?.state === "complete"
    && coverage.sync.retainedRecords === 0 && !retained
    && folders.every((folder) => typeof folder?.query === "string" && folder.query.trim()
      && folder.completeWithinQuery === true && folder.truncated === false);
  const incomplete = coverage?.sync?.state === "in-progress" || coverage?.sync?.state === "limited"
    || retained || folders.some((folder) => folder?.completeWithinQuery === false || folder?.truncated === true);
  const state = freshness === "stale" ? "stale-index" : incomplete ? "partial-index"
    : complete ? "complete-within-configured-queries" : "coverage-unknown";
  return {
    scope: "current-local-email-index",
    mailboxTotal: null,
    canAnswerMailboxTotal: false,
    state,
    freshness,
    notice: "The Gmail mailbox total is unknown. count is this page's returned records; totalMatches and hasMore "
      + "describe only matches in the local index, never the account total. "
      + "hasMore means more pages of this index, not incomplete synchronization; use counting.state and coverage for synchronization. "
      + (state === "partial-index" ? "Synchronization is incomplete or earlier-scan cache remains; current absence is not established. "
        : state === "stale-index" ? "The index is stale; current absence is not established. Complete flags describe a historical scan; current synchronization is unverified. "
        : state === "complete-within-configured-queries" ? "Coverage is complete only within the configured folder queries, at the snapshot time. "
        : "Freshness or coverage is unknown; current absence is not established. ")
      + "Search and absence cover projected fields only, not all email content.",
    indexedMatches: Number.isSafeInteger(indexedMatches) && indexedMatches >= 0 ? indexedMatches : null,
    configuredQueries: required.map((folder, index) => ({ folder,
      query: typeof folders[index]?.query === "string" ? folders[index].query : null })),
  };
}

export function countEmail(data, query, sync, now, staleAfterMs) {
  if (data?.enabled !== true || data.coverage?.available === false || !Array.isArray(data.records)
      || data.recordCount !== undefined && (!Number.isSafeInteger(data.recordCount) || data.recordCount !== data.records.length)) {
    return unavailableCount("gmail");
  }
  const ids = new Set();
  for (const record of data.records) {
    if (!record || typeof record !== "object" || Array.isArray(record)
        || typeof record.id !== "string" || !record.id || record.id.length > 256 || ids.has(record.id)
        || !["threadId", "subject", "from", "to", "cc", "summary"].every(key => typeof record[key] === "string")
        || !Array.isArray(record.mailboxes) || !record.mailboxes.length
        || record.mailboxes.some(value => !["inbox", "sent"].includes(value))
        || !Array.isArray(record.labelIds) || record.labelIds.some(value => typeof value !== "string")) {
      return unavailableCount("gmail");
    }
    ids.add(record.id);
  }
  const metadata = countMetadata(data, sync, now, staleAfterMs);
  const indexedMatchCount = searchEmail(data.records, query).length;
  const scoped = emailCountScope({ ...data, stale: metadata.freshness === "unknown" ? undefined : metadata.freshness === "stale" }, query, indexedMatchCount);
  const complete = scoped.state === "complete-within-configured-queries" && metadata.syncState === "ok";
  if (scoped.state === "partial-index") metadata.limitations.push("source-partial");
  if (scoped.state === "coverage-unknown") metadata.limitations.push("coverage-unknown");
  return { ...metadata, source: "gmail", scope: "current-local-email-index", filterSemantics: "sanitized-projected-fields",
    queryApplied: typeof query === "string" && Boolean(query.trim()), indexedMatchCount,
    mailboxTotal: null, canAnswerMailboxTotal: false, coverageState: scoped.state,
    completeWithinConfiguredQueries: complete,
    absenceWithinCoveredProjection: complete && indexedMatchCount === 0 };
}
