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
