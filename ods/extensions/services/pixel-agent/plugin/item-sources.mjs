// Per-item sources at finalization: one bounded continuation, no host reads.
//
// When the owner asks for a source per item ("for each ... a direct official
// source URL"), an answer that supports an item only with a listing page (a
// calendar, a season guide, an aggregator's list: source-kind.mjs) has not
// given that item its own source. tower1 round 091 (2026-09-25) cited an
// aggregator's live-music list for one event and one Visit Philadelphia season
// guide for two others, after three tool calls; every cited page had been
// read, so the read-receipt check passed it. The guide links each entry's
// title to the entry's own site, and pixel_ods_search_read now keeps those
// links, so the model is asked once to read and cite the items' own pages,
// with the candidate URLs it already saw, in one urls call.
//
// An item passes when its cited page is not a listing, when the page is the
// item's own (its host is named after the item, or its path names it twice:
// an organizer's program or a venue's detail page with a sidebar), or when
// the answer says plainly that the item's own page was unavailable ("the
// official site returned 403"). The check never reads a page, never replaces
// the answer and asks at most once per run (tool-loop-guard.mjs).
import {citationItems} from './citation-verification.mjs';
import {citationKey, publicSourceUrl} from './completion-assurance.mjs';
import {entryWords, urlNamesEntry, urlWords} from './source-kind.mjs';

export const ITEM_SOURCE_LIMITS = Object.freeze({
  maxFindings: 6,
  maxCandidates: 5,   // one pixel_ods_search_read urls call
  labelChars: 80,
});

const fold = value => String(value ?? '').normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase()
  .replace(/[‘’ʼ`´']/g, '');

// The answer says an item's own page was unavailable, or that its source is
// not the official one.
const UNAVAILABLE = new RegExp('\\b(?:unavailable|not available|inaccessible|' +
  'no (?:official|direct|own|separate|dedicated|event) (?:page|site|website|source|url|link|listing)|' +
  'not (?:an? |the )?(?:official|direct|primary)\\b|' +
  '(?:could ?not|couldnt|cannot|cant|unable to|failed to|did not|didnt|was not able to)\\s+(?:be\\s+)?' +
  '(?:open|read|load|reach|access|fetch|find|locate|verify|confirm)\\w*|' +
  '(?:returned|returns|gave|answered with|responded with)\\s+(?:an?\\s+)?(?:http\\s*)?(?:[45]\\d\\d|error|access denied|forbidden|a bot)|' +
  'access denied|forbidden|bot[- ]?(?:protection|check|wall|challenge)|blocked|' +
  'not (?:verified|opened|read|found)|' +
  'nao (?:foi possivel|consegui|encontr|verific|abert|lid)\\w*|indisponivel|sem (?:pagina|site|fonte) oficial)', 'i');

// The item's display label: its first line with a name, without Markdown,
// list numbering or a field label. It is the answer's own text.
function itemLabel(text) {
  for (const raw of String(text).split('\n')) {
    const line = raw.replace(/[*_`#>|[\]]/g, ' ').replace(/^\s*(?:[-+]|\d{1,3}[.)])\s+/, '')
      .replace(/^\s*(?:event|evento|item)\s*\d{0,3}\s*[:.)\-–—]\s*/i, '')
      .replace(/^\s*(?:title|name|event title|titulo|nome)\s*:\s*/i, '').replace(/\s+/g, ' ').trim();
    if (!/\p{L}/u.test(line)) continue;
    return line.length > ITEM_SOURCE_LIMITS.labelChars
      ? `${line.slice(0, ITEM_SOURCE_LIMITS.labelChars - 1).trimEnd()}…` : line;
  }
  return 'item';
}

// Whether a cited page is the item's own: its host is named after the item
// (two of its words, or one that is most of a host label: easternstate.org,
// designphiladelphia.org, but not visitphilly.com for "Philly Music Fest"), or
// its path names the item twice (a detail page's slug).
function ownPage(href, words) {
  const {host, path} = urlWords(href);
  const pathWords = new Set(path);
  const inHost = words.filter(word => word.length >= 4 && host.some(label => label.includes(word)));
  return inHost.length >= 2 ||
    inHost.some(word => host.some(label => label.includes(word) && word.length >= 0.6 * label.length)) ||
    words.filter(word => pathWords.has(word)).length >= 2;
}

// Items whose only cited source is a listing page. `listings` maps citation
// keys to {url, items, ownLinks}; `requestText` is the owner's request, whose
// own words (the city, "events") never identify an item.
export function itemSourceFindings(answer, {listings, requestText = ''} = {}) {
  if (!(listings instanceof Map) || !listings.size) return [];
  const text = String(answer ?? '');
  const request = new Set(entryWords(requestText));
  const items = citationItems(text);
  // Sentences that admit a limitation, with their words, for items whose
  // limitation is stated away from their own block (a closing note).
  const admissions = text.split(/\n+|(?<=[.!?])\s+/).filter(sentence => UNAVAILABLE.test(fold(sentence)))
    .map(sentence => new Set(fold(sentence).split(/[^\p{L}\p{N}]+/u)));
  const findings = [];
  const seen = new Set();
  for (const item of items) {
    const listing = listings.get(item.key);
    if (!listing) continue;
    const words = [...new Set([...item.tokens, ...entryWords(itemLabel(item.text))])]
      .filter(word => word.length >= 3 && !request.has(word));
    if (!words.length || ownPage(item.href, words)) continue;
    if (UNAVAILABLE.test(fold(item.text))) continue;
    if (admissions.some(sentence => words.some(word => word.length >= 4 && sentence.has(word)))) continue;
    const label = itemLabel(item.text);
    const id = `${item.key}\n${label}`;
    if (seen.has(id)) continue;
    seen.add(id);
    // The own page the listings link for this item: the link named after
    // most of its words.
    let candidate, best = 0;
    for (const page of listings.values()) {
      for (const link of page.ownLinks) {
        if (citationKey(link) === item.key) continue;
        const score = urlNamesEntry(link, words).length;
        if (score > best) { best = score; candidate = link; }
      }
    }
    findings.push({label, href: publicSourceUrl(item.href) ?? item.href, items: listing.items,
      ...(candidate ? {candidate} : {})});
    if (findings.length >= ITEM_SOURCE_LIMITS.maxFindings) break;
  }
  return findings;
}

export const ITEM_SOURCE_REASON = 'The owner asked for a source per item; some items cite only a listing page.';

// The one continuation. Every value in it is a URL the run already saw or the
// answer's own item label.
export function itemSourceRevision(findings) {
  const candidates = [...new Set(findings.map(finding => finding.candidate).filter(Boolean))]
    .slice(0, ITEM_SOURCE_LIMITS.maxCandidates);
  const lines = findings.map(finding => `- "${finding.label.replace(/"/g, "'")}" cites ${finding.href}, a listing of ` +
    `${finding.items} dated entries. ${finding.candidate && candidates.includes(finding.candidate)
      ? `Its own page, linked from a listing you read: ${finding.candidate}` : 'No own page for it was seen yet.'}`);
  const instruction = [
    'The owner asked for a direct source for each item. These items cite only a listing page (a page that names many ' +
      'dated entries, such as a calendar, guide or aggregator), which is a lead, not the item\'s own page:',
    ...lines,
    (candidates.length
      ? `Read those own pages in one pixel_ods_search_read call with urls ${JSON.stringify(candidates)}. `
      : '') +
      'For an item without one, search once for its own page (the organizer\'s, venue\'s or official event page) or ' +
      'replace the item with one whose own page you read. Then cite each item\'s own page next to its facts, using ' +
      'only facts that page shows. If an item\'s own page cannot be found or read, keep the listing as its source only ' +
      'with that limitation stated next to the item (for example: official page returned HTTP 403). Do not guess URLs, ' +
      'do not repeat failed reads, and keep the rest of your answer.',
  ].join('\n');
  return {action: 'revise', reason: ITEM_SOURCE_REASON, retry: {
    idempotencyKey: 'ods-item-own-sources', maxAttempts: 1, instruction}};
}
