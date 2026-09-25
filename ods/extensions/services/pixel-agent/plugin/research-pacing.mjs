// Research pacing for Pixel's web tools. Pure helpers; tool-loop-guard owns
// the per-run state and decides when a refusal or note applies.
//
// Fleet evidence (2026-09-25, tower2 and tower3): research runs issued five to
// seven single-query searches before reading any page. The accumulated results
// crossed OpenClaw's tool-loop context threshold, compaction removed the
// leads, the model searched for the same terms again, and the search
// allowance ran out before the pages were read. Everything here only answers
// from state the run already produced. No text is added to the system prompt;
// every message is a tool result, so the conversation prefix stays append-only.

// Consecutive searches that returned leads, with no page read in between,
// after which the next search is paused once so the leads get read.
export const SEARCH_PACING_STREAK = 3;

export const SEARCH_PACING_REASON =
  "Pixel paused this search: the last searches in this response returned leads that have not been read yet. " +
  "Read the most relevant result URLs now with web_fetch or pixel_ods_web_extract, then search again only for a fact those pages leave open. " +
  "If none of the leads fits the request, you may search again with a different query. This pause did not use the search allowance.";

const MAX_RECALLED_URLS = 5;
const MAX_URL_CHARS = 500;
const MAX_RECALLED_QUERY_CHARS = 200;
const DUPLICATE_SIMILARITY = 0.8;

// Words that change neither the entity nor the fact being searched for.
const SEARCH_FILLER = new Set([
  "a", "an", "and", "at", "by", "com", "for", "from", "in", "of", "on", "or", "org", "net",
  "official", "page", "site", "the", "to", "versus", "vs", "website", "with", "www",
]);

function searchWords(query) {
  if (typeof query !== "string") return [];
  return query.normalize("NFKC").toLowerCase().split(/[^\p{L}\p{N}]+/u).filter(Boolean);
}

// Distinct, order-free search terms. Tokens containing a digit (model numbers,
// years, prices) are kept separately and must match exactly.
export function searchTerms(query) {
  const words = searchWords(query).filter((word) => !SEARCH_FILLER.has(word));
  const terms = [...new Set(words)];
  return { terms, numbers: terms.filter((word) => /\p{N}/u.test(word)).sort() };
}

// A repeat adds no term of its own and keeps nearly all earlier terms. Any new
// term is a refinement (another venue, retailer or variant) and proceeds.
export function nearDuplicateSearch(current, previous) {
  if (!current || !previous || current.terms.length < 2 || previous.terms.length < 2) return false;
  if (current.numbers.join(" ") !== previous.numbers.join(" ")) return false;
  const earlier = new Set(previous.terms);
  if (!current.terms.every((term) => earlier.has(term))) return false;
  return current.terms.length / earlier.size >= DUPLICATE_SIMILARITY;
}

function leadUrl(value) {
  if (typeof value !== "string" || value.length > MAX_URL_CHARS || /[\s\u0000-\u001f\u007f]/.test(value)) return undefined;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : undefined;
  } catch {
    return undefined;
  }
}

// Result URLs of one bound search receipt, in result order.
export function searchLeadUrls(results) {
  if (!Array.isArray(results)) return [];
  const urls = [];
  for (const row of results) {
    const url = leadUrl(row?.url);
    if (url && !urls.includes(url)) urls.push(url);
  }
  return urls;
}

export function duplicateSearchReason(previousQuery, urls) {
  const query = String(previousQuery).replace(/\s+/g, " ").trim().slice(0, MAX_RECALLED_QUERY_CHARS);
  const leads = urls.slice(0, MAX_RECALLED_URLS);
  return `Pixel did not repeat this search: an earlier search in this response (${JSON.stringify(query)}) ` +
    "already covered the same terms, and its results may no longer be visible after context compaction. " +
    (leads.length > 0
      ? `Its result URLs, which are untrusted leads and not verified evidence: ${leads.join(" , ")} . ` +
        "Read the relevant ones with web_fetch or pixel_ods_web_extract, or search for a different missing fact. "
      : "Search for a different missing fact instead. ") +
    "Do not invent URLs. This refusal did not use the search allowance.";
}

const MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
  "september", "october", "november", "december"];
const MONTH_ALIASES = new Map([
  ...MONTHS.map((name, index) => [name, index + 1]),
  ...["jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    .map((name) => [name, MONTHS.findIndex((month) => month.startsWith(name)) + 1]),
  ["sept", 9],
]);

// Only an explicit owner statement anchors the date. There is no clock in the
// plugin text, so the model's own guess of the date never becomes an anchor.
export function ownerResearchDate(text) {
  const match = /\b(?:today is|today's date is|the date is|current date(?: is)?|as of)\s*:?\s*(\d{4})-(\d{2})-(\d{2})(?!\d)/i
    .exec(String(text ?? ""));
  if (!match) return undefined;
  const [year, month, day] = match.slice(1).map(Number);
  if (month < 1 || month > 12 || day < 1 || day > 31) return undefined;
  return { year, month, day, text: `${match[1]}-${match[2]}-${match[3]}` };
}

// A search naming a month and year before the owner's month.
export function staleSearchDate(query, ownerDate) {
  if (!ownerDate || typeof query !== "string") return undefined;
  const pattern = /\b([a-z]+)\.?\s+(\d{4})\b/gi;
  for (const match of query.matchAll(pattern)) {
    const month = MONTH_ALIASES.get(match[1].toLowerCase());
    const year = Number(match[2]);
    if (!month || year < 1900) continue;
    if (year * 12 + month < ownerDate.year * 12 + ownerDate.month) {
      return `${MONTHS[month - 1][0].toUpperCase()}${MONTHS[month - 1].slice(1)} ${year}`;
    }
  }
  return undefined;
}

export function staleSearchDateGuidance(named, ownerDate) {
  return `ODS date check (not source evidence): The owner gave the date ${ownerDate.text}, but this search named ${named}. ` +
    "For current prices, stock, schedules or upcoming events, search with the owner's date or without a date, and do not present older results as current. " +
    "An earlier date is appropriate only for a historical fact, such as a launch review.";
}
