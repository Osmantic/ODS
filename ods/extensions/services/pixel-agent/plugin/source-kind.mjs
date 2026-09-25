// Whether a read page is one item's own page or a listing of many items.
// Pure text helpers, no imports: pixel_ods_search_read (search-read.mjs) marks
// its pages with them, the read ledger (completion-assurance.mjs) records
// them, and the per-item source check (item-sources.mjs) uses them.
//
// Fleet evidence (tower1 round 091, 2026-09-25): asked for "a direct official
// source URL" for each of three Philadelphia events, Pixel answered after one
// search_read and two re-reads of listing pages, citing an aggregator's live
// music list for one event and the same Visit Philadelphia season guide for
// two. The guide links each entry's title to the entry's own site
// (designphiladelphia.org, easternstate.org); search_read printed only
// same-site links, so those own pages never reached the model.

export const SOURCE_KIND_LIMITS = Object.freeze({
  listingItems: 5,   // distinct dated entries that make a page a listing
  maxLines: 5000,
  maxLineChars: 600,
  headingLookback: 3, // lines above a bare date that may name its entry
  entryDateChars: 400, // an entry's own link has a date this close to it
  maxItemLinks: 48,   // own-page links kept per listing page (host-side details)
});

const fold = value => String(value ?? '').normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase();

const MONTH = '(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)';
// A calendar date: "Oct 2", "2 October", "2026-10-02", "10/2[/2026]" (never
// "24/7" or a longer slash chain), or a date block's text run together, as
// some calendars extract ("Oct012026").
const DATE_SOURCE = `\\b${MONTH}\\.?\\s+\\d{1,2}(?:st|nd|rd|th)?\\b|\\b\\d{1,2}(?:st|nd|rd|th)?\\s+(?:of\\s+)?${MONTH}\\b|` +
  `\\b${MONTH}(?:0[1-9]|[12]\\d|3[01])(?:19|20)\\d{2}|` +
  '\\b\\d{4}-\\d{2}-\\d{2}\\b|(?<![\\d/])(?:0?[1-9]|1[0-2])/(?:0?[1-9]|[12]\\d|3[01])(?:/(?:\\d{4}|\\d{2}))?(?![\\d/])';
const DATE = new RegExp(DATE_SOURCE, 'i');
const DATES = new RegExp(DATE_SOURCE, 'gi');
export const hasDate = text => DATE.test(String(text ?? ''));
const TIMES = /\b\d{1,2}(?::\d{2})?\s?[ap]\.?\s?m\b\.?/gi;

// Words that never tell one entry from another: calendar words, list and
// ticketing boilerplate, and connecting words.
const FILLER = new Set((
  'january february march april may june july august september october november december jan feb mar apr jun jul ' +
  'aug sep sept oct nov dec monday tuesday wednesday thursday friday saturday sunday mon tue tues wed thu thur ' +
  'thurs fri sat sun today tonight tomorrow weekend week weeks month year daily through thru until till from ' +
  'the and for with this that at on in of to by an or via its is are was be onwards happening select dates date ' +
  'vary varies time times doors door open opens start starts starting begins ends end ongoing all day days night ' +
  'nights pm am edt est et pt ct utc event events more info information details detail read view see buy get ' +
  'tickets ticket book booking reserve rsvp free price prices add calendar save share interested going photo ' +
  'photos courtesy credit here link links website site page official').split(' '));

// Link labels that are an action, never an entry's title.
const ACTION = new Set(('buy ticket tickets shop book booking reserve rsvp register registration directions map maps ' +
  'share follow subscribe donate login sign signin signup download print email cart checkout').split(' '));
const OFFICIAL_LABEL = /^(?:(?:visit|go to)\s+)?(?:the\s+)?(?:official\s+(?:web\s?site|site|page|event\s+page)|(?:event|festival|show)\s+(?:web\s?site|site|page)|web\s?site)$/;

// Hosts whose pages are never an entry's own page for a text reader.
const NOT_OWN_HOSTS = ['facebook.com', 'instagram.com', 'x.com', 'twitter.com', 'tiktok.com', 'youtube.com',
  'youtu.be', 'linkedin.com', 'pinterest.com', 'threads.net', 'maps.google.com', 'goo.gl', 'maps.apple.com',
  'bing.com', 'google.com'];
const onHost = (host, domain) => host === domain || host.endsWith(`.${domain}`);

// Distinct words of at least three letters, folded, without digits or filler.
export function entryWords(text) {
  const words = [];
  for (const word of fold(text).split(/[^\p{L}\p{N}]+/u)) {
    if (word.length < 3 || /\d/.test(word) || FILLER.has(word) || words.includes(word)) continue;
    words.push(word);
  }
  return words;
}

// A listing names many distinct dated entries: an events calendar, a
// season guide, an aggregator's list. Each dated line is keyed by its own
// words once dates, times and filler are removed; a bare date line ("October
// 1-11, 2026") takes the nearest line above it that names something and is
// not already an entry's key (a photo credit or label repeated on every
// entry), else the nearest. The same entry repeated (an FAQ restating the
// list, a schedule of one event's dates) counts once.
export function listingProfile(text) {
  const L = SOURCE_KIND_LIMITS;
  const lines = String(text ?? '').split('\n', L.maxLines);
  const keys = new Set();
  const above = [];
  for (const raw of lines) {
    const line = raw.slice(0, L.maxLineChars).trim();
    if (!line) continue;
    if (!DATE.test(line)) {
      const words = entryWords(line);
      if (words.length) {
        above.push(words.slice(0, 8).join(' '));
        if (above.length > L.headingLookback) above.shift();
      }
      continue;
    }
    const words = entryWords(line.replace(DATES, ' ').replace(TIMES, ' '));
    const key = words.length ? words.slice(0, 8).join(' ')
      : [...above].reverse().find(candidate => !keys.has(candidate)) ?? above.at(-1);
    if (key) keys.add(key);
  }
  return {items: keys.size, listing: keys.size >= L.listingItems};
}

// The words a URL is named with: its host labels (without "www", the
// top-level domain and a second-level "co"/"com"/"org"..., hyphens removed)
// and its path words.
export function urlWords(value) {
  let url;
  try { url = new URL(value); } catch { return {host: [], path: []}; }
  const labels = url.hostname.toLowerCase().replace(/^www\d?\./, '').split('.');
  labels.pop();
  if (labels.length > 1 && ['co', 'com', 'org', 'net', 'gov', 'ac', 'edu'].includes(labels.at(-1))) labels.pop();
  const host = labels.map(label => label.replace(/-/g, '')).filter(label => label.length >= 3);
  let path = '';
  try { path = decodeURIComponent(url.pathname); } catch { path = url.pathname; }
  return {host, path: fold(path).split(/[^\p{L}\p{N}]+/u).filter(word => word.length >= 3 && !/^\d+$/.test(word))};
}

// Whether a URL is named after the entry: one of the entry's distinctive
// words (four letters or more) is in a host label, or is a path word.
// "Halloween Nights at Eastern State Penitentiary" names easternstate.org;
// "DesignPhiladelphia Festival" names designphiladelphia.org.
export function urlNamesEntry(value, words) {
  const {host, path} = urlWords(value);
  const pathWords = new Set(path);
  return words.filter(word => word.length >= 4 &&
    (host.some(label => label.includes(word)) || pathWords.has(word)));
}

// The registrable part of a host name, approximately: its last two labels,
// or three under a two-letter country code's "co", "com", "org"... A link to
// support.allevents.in from allevents.in is the same site.
export function siteOf(host) {
  const labels = String(host ?? '').toLowerCase().replace(/^www\./, '').replace(/\.$/, '').split('.');
  const country = labels.length > 2 && labels.at(-1).length === 2 &&
    ['co', 'com', 'org', 'net', 'gov', 'ac', 'edu', 'or', 'ne', 'go'].includes(labels.at(-2));
  return labels.slice(country ? -3 : -2).join('.');
}

export function ownPageHost(value) {
  try {
    const host = new URL(value).hostname.toLowerCase().replace(/^www\./, '');
    return !NOT_OWN_HOSTS.some(domain => onHost(host, domain));
  } catch {
    return false;
  }
}

// An off-site link on a listing that points at one entry's own page: it sits
// by a dated entry (`around` is the page text on either side of it), its
// label is not an action ("Buy tickets"), and it is either an "official site"
// link or the entry's title (it fills most of its line, as a heading does)
// naming the link's host or path. An address linking to a map, a press award
// in a sentence, a ticket seller's button or a footer partner is not.
export function entryOwnLink({label, line, url, around}) {
  if (!ownPageHost(url) || !hasDate(around)) return false;
  const folded = fold(label).replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
  if (!folded || folded.split(' ').some(word => ACTION.has(word))) return false;
  if (OFFICIAL_LABEL.test(folded)) return true;
  const text = String(line ?? '').replace(/\s+/g, ' ').trim();
  if (!text || String(label).trim().length < 0.5 * text.length) return false;
  return urlNamesEntry(url, entryWords(label)).length > 0;
}
