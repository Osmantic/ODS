// Host verification of cited-but-unread source pages at finalization.
//
// A research answer often cites event detail links it saw on a listing page
// without opening them. Instead of spending another model turn, the host reads
// at most four such public pages itself, through the same guarded reader as
// pixel_ods_web_extract (web-extract.mjs), under a short total time budget. A
// page counts only when the fetch succeeds with a text document AND its text
// carries claim anchors taken from the answer next to that citation: the
// distinctive title tokens plus the date (or, without a date, the numbers)
// that the list item, section or sentence attributes to the URL. The anchors
// never come from the URL itself, so a slug such as flyers-capitals-9-26-26 is
// matched only by the page's own display title and date. In doubt, it fails.
// A verified page is recorded as a distinct host-verification receipt, never
// as a model read. This checks that the cited page supports the attributed
// claim; it is not proof of every statement in the answer.
import {citationKey, citationSpans} from './completion-assurance.mjs';
import {PUBLIC_PAGE_TEXT_TYPES} from './web-extract.mjs';

export const HOST_CITATION_LIMITS = Object.freeze({
  maxUrls: 4,          // per answer; more unread citations means no host check
  maxUrlsPerRun: 8,    // an answer after a revision may add new ones, never retries
  budgetMs: 4000,      // total wall clock for the parallel reads
  windowChars: 400,    // title tokens and date must co-occur within this span
  maxTitleTokens: 6,
  maxPageChars: 400000,
});

const fold = value => String(value ?? '').normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase()
  .replace(/[‘’ʼ`´']/g, '');

const EN_MONTHS = {january: 1, jan: 1, february: 2, feb: 2, march: 3, mar: 3, april: 4, apr: 4, may: 5, june: 6,
  jun: 6, july: 7, jul: 7, august: 8, aug: 8, september: 9, sept: 9, sep: 9, october: 10, oct: 10, november: 11,
  nov: 11, december: 12, dec: 12};
const PT_MONTHS = {janeiro: 1, fevereiro: 2, fev: 2, marco: 3, abril: 4, abr: 4, maio: 5, mai: 5, junho: 6,
  julho: 7, agosto: 8, ago: 8, setembro: 9, set: 9, outubro: 10, out: 10, novembro: 11, dezembro: 12, dez: 12};
const MONTHS = {...PT_MONTHS, ...EN_MONTHS};
// Portuguese abbreviations that are also ordinary English words ("5 out of 10")
// need "de" before them or a year after them.
const AMBIGUOUS_MONTHS = new Set(['set', 'out', 'mai', 'dez', 'ago', 'fev', 'abr']);
const alternation = names => Object.keys(names).sort((a, b) => b.length - a.length).join('|');
const ORDINAL = '(?:st|nd|rd|th|º|o)?';
const MONTH_FIRST = new RegExp(`\\b(${alternation(EN_MONTHS)})\\.?\\s+(\\d{1,2})${ORDINAL}\\b` +
  `(?:\\s*(?:-|–|—|to|through|&|and)\\s*\\d{1,2}${ORDINAL}\\b)?(?:,?\\s+(\\d{4})\\b)?`, 'g');
const DAY_FIRST = new RegExp(`\\b(\\d{1,2})${ORDINAL}(?:\\s*(?:-|–|—|a|to|&|e|and)\\s*\\d{1,2}${ORDINAL})?` +
  `\\s*(de\\s+)?(${alternation(MONTHS)})\\b\\.?(?:,?\\s*(?:de\\s+)?(\\d{4})\\b)?`, 'g');
const ISO_DATE = /\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b/g;
const NUMERIC_DATE = /\b(\d{1,2})([/.])(\d{1,2})\2(\d{4})\b/g;
const TIME_12 = /\b(\d{1,2})(?:[:.](\d{2}))?\s*([ap])\.?\s?m\b\.?/g;
const TIME_24 = /\b([01]?\d|2[0-3])[:h]([0-5]\d)\b(?!\s*[ap]\.?\s?m\b)/g;

const validDate = (y, m, d) => m >= 1 && m <= 12 && d >= 1 && d <= 31 && (y === undefined || (y >= 1900 && y <= 2100));

// Dates with offsets in folded text. An all-numeric date whose day and month
// could be swapped is read in the answer's language, and ignored on a page.
function datesIn(text, {portuguese = false, page = false} = {}) {
  const found = [];
  const add = (match, y, m, d) => {
    if (validDate(y, m, d)) found.push({index: match.index, end: match.index + match[0].length, y, m, d});
  };
  for (const match of text.matchAll(ISO_DATE)) add(match, +match[1], +match[2], +match[3]);
  for (const match of text.matchAll(MONTH_FIRST)) add(match, match[3] ? +match[3] : undefined, EN_MONTHS[match[1]], +match[2]);
  for (const match of text.matchAll(DAY_FIRST)) {
    if (AMBIGUOUS_MONTHS.has(match[3]) && !match[2] && !match[4]) continue;
    add(match, match[4] ? +match[4] : undefined, MONTHS[match[3]], +match[1]);
  }
  for (const match of text.matchAll(NUMERIC_DATE)) {
    const [a, b, y] = [+match[1], +match[3], +match[4]];
    if (match[2] === '.' || a > 12) add(match, y, b, a);
    else if (b > 12 || a === b) add(match, y, a, b);
    else if (!page) add(match, y, portuguese ? b : a, portuguese ? a : b);
  }
  found.sort((x, y) => x.index - y.index || y.end - x.end);
  return found.filter((date, i) => !found.slice(0, i).some(other => date.index < other.end));
}

// Minutes after midnight. A bare "8:45" without a meridiem is ambiguous: it is
// never an answer anchor, and on a page it may stand for either reading.
function timesIn(text, {page = false} = {}) {
  const found = [];
  for (const match of text.matchAll(TIME_12)) {
    const hour = +match[1], minute = match[2] ? +match[2] : 0;
    if (hour < 1 || hour > 12 || minute > 59) continue;
    found.push({index: match.index, end: match.index + match[0].length,
      minutes: [(hour % 12 + (match[3] === 'p' ? 12 : 0)) * 60 + minute]});
  }
  for (const match of text.matchAll(TIME_24)) {
    if (found.some(time => match.index < time.end && time.index < match.index + match[0].length)) continue;
    const hour = +match[1], minute = +match[2];
    const unambiguous = hour === 0 || hour >= 13 || match[0].includes('h');
    if (!unambiguous && !page) continue;
    found.push({index: match.index, end: match.index + match[0].length,
      minutes: unambiguous ? [hour * 60 + minute] : [hour * 60 + minute, (hour + 12) * 60 + minute]});
  }
  return found.sort((a, b) => a.index - b.index);
}

const STOPWORDS = new Set(('a an and are as at be by for from in into is it its of on or the to vs versus with via plus up ' +
  'o os as um uma uns umas de da do das dos e em no na nos nas com para por pelo pela ao aos ou se sem sobre').split(' '));
const GENERIC = new Set(('event events evento eventos show shows concert concerts concerto concertos tour turne live ' +
  'ticket tickets ingresso ingressos official oficial presents presented presenting apresenta apresentado featuring feat ft ' +
  'special guest guests performance performances').split(' '));
const CALENDAR = new Set([...Object.keys(MONTHS), ...('monday tuesday wednesday thursday friday saturday sunday mon tue tues ' +
  'wed thu thur thurs fri sat sun segunda terca quarta quinta sexta sabado domingo feira am pm').split(' ')]);
const LABEL_WORDS = new Set([...GENERIC, ...STOPWORDS, ...('title titles name date dates time times exact direct source sources url ' +
  'urls link links website site page venue location address where when notes note broadcast price prices status category type ' +
  'details detail info information verified verification method search limitations unavailable results additional available ' +
  'more other data local fonte fontes horario hora endereco onde quando observacoes observacao categoria tipo preco pagina ' +
  'titulo nome evento atracao artist artista performer').split(' ')]);
const DATE_LABEL = /\b(?:date|dates|when|data|quando|dia)\s*(?:\*\*|__)?\s*[:：]/i;
const TITLE_FIELD = /^\s*(?:[-*+]\s+|\d{1,3}[.)]\s+)?(?:\*\*|__)?\s*(?:event\s+|evento\s+)?(?:title|name|titulo|título|nome|event|evento|show|artist|artista|performer|atração|atracao)\s*(?:(?:\*\*|__)\s*[:：]|[:：]\s*(?:\*\*|__)?)\s*(.+)$/i;
const GENERIC_LINK_TEXT = /^(?:source|sources|link|here|tickets?|official (?:page|site|source)|website|site|page|fonte|fontes|aqui|pagina|página|ingressos?|more|details|mais)$/i;

const HEADING = /^\s{0,3}#{1,6}\s+(.*)$/;
const RULE = /^\s{0,3}(?:[-*_]\s*){3,}$/;
const ITEM = /^(\s*)(?:[-*+]|\d{1,3}[.)])\s+/;
const TABLE_ROW = /^\s*\|.*\|\s*$/;
const indentOf = text => (text.match(/^\s*/)[0].replace(/\t/g, '    ')).length;
const blank = line => !line.text.trim();

// Tokens of a title phrase: parenthetical qualifiers, "Event 1:" numbering and
// Markdown emphasis are dropped, as are stopwords, generic event words, month
// and weekday names and bare numbers (dates are anchors of their own).
function titleTokens(phrase, {properOnly = false} = {}) {
  let value = String(phrase ?? '').replace(/\*\*|__|`/g, '').replace(/\([^)]*\)|\[[^\]]*\]/g, ' ')
    .replace(/^\s*(?:(?:event|evento|item|option|opcao|opção|#)\s*)?\d{1,3}\s*[:.)\-–—]\s*/i, '');
  if (properOnly) value = value.split(/\s+/).filter(word => /^["'“‘(]?[\p{Lu}\p{N}]/u.test(word)).join(' ');
  const tokens = [];
  for (const token of fold(value).split(/[^a-z0-9]+/)) {
    if (token.length < 2 || /^\d+$/.test(token) || /^\d+(?:st|nd|rd|th)$/.test(token) || STOPWORDS.has(token) ||
        GENERIC.has(token) || CALENDAR.has(token) || tokens.includes(token)) continue;
    tokens.push(token);
    if (tokens.length >= HOST_CITATION_LIMITS.maxTitleTokens) break;
  }
  return tokens;
}

// Field and section labels ("Date", "Official Source URL", "Notes on
// Verification") name a slot, not the claim's subject.
function labelOnly(text) {
  const words = fold(text).split(/[^a-z0-9]+/).filter(Boolean);
  return words.length > 0 && words.length <= 6 && words.every(word => LABEL_WORDS.has(word) || /^\d+$/.test(word));
}

function boldSpans(text) {
  const spans = [];
  for (const match of text.matchAll(/(\*\*|__)([^*_\n]{1,200}?)\1(\s*[:：])?/g)) {
    const inner = match[2].trim();
    const colon = Boolean(match[3]) || /[:：]$/.test(inner);
    const label = colon && labelOnly(inner.replace(/[:：]$/, ''));
    spans.push({text: inner.replace(/[:：]$/, ''), label});
  }
  return spans;
}

// A line's text without its list marker and without a leading field label.
function leadText(line) {
  const text = line.replace(ITEM, '');
  const labelled = text.match(/^\s*(?:\*\*|__)?([^:：*_\n]{1,40}?)(?:\*\*|__)?\s*[:：]\s*(?:\*\*|__)?\s*/);
  return labelled && labelOnly(labelled[1]) ? text.slice(labelled[0].length) : text;
}

// The title the segment gives its claim, most explicit first: a title field,
// a heading, the first non-label bold text, the citation's own link text, a
// table row's first text cell, then the proper nouns leading the sentence.
function segmentTitle(segment, {linkText, unitText}) {
  const lines = segment.split('\n');
  for (const line of lines) {
    const field = line.match(TITLE_FIELD)?.[1];
    if (field && titleTokens(field).length) return titleTokens(field);
  }
  for (const line of lines) {
    const heading = line.match(HEADING)?.[1]?.replace(/\*\*|__/g, '').replace(/[:：]\s*$/, '');
    if (heading && !labelOnly(heading) && titleTokens(heading).length) return titleTokens(heading);
  }
  for (const span of boldSpans(segment)) if (!span.label && titleTokens(span.text).length) return titleTokens(span.text);
  if (linkText && !GENERIC_LINK_TEXT.test(linkText.trim()) && titleTokens(linkText).length) return titleTokens(linkText);
  if (TABLE_ROW.test(unitText)) {
    for (const cell of unitText.split('|').map(cell => cell.trim()).filter(Boolean)) {
      if (!datesIn(fold(cell)).length && !/^[\d\s.,:$€£%-]+$/.test(cell) && titleTokens(cell).length) return titleTokens(cell);
    }
    return [];
  }
  const lead = leadText(unitText.trim().split('\n')[0]).split(/\s[—–-]\s|[:：]\s|\s\|\s|\s\(|,\s|;\s/)[0];
  return titleTokens(lead, {properOnly: true});
}

function numbersIn(folded) {
  const text = folded.replace(/(\d),(?=\d{3}\b)/g, '$1');
  const numbers = [];
  for (const match of text.matchAll(/(?<![\w.])\d+(?:\.\d+)?(?![\w])/g)) {
    const value = match[0];
    if (/^(?:19|20)\d{2}$/.test(value) || numbers.includes(value)) continue;
    numbers.push(value);
  }
  return numbers;
}

// Anchors of one citation segment (URLs already masked out of it).
function anchorsOf(segment, {portuguese, linkText, unitText}) {
  const tokens = segmentTitle(segment, {linkText, unitText});
  const folded = fold(segment);
  const dates = datesIn(folded, {portuguese});
  let date, time;
  if (dates.length) {
    // Prefer the date on a "Date:" line; otherwise the first date. Offsets are
    // in the folded text (folding drops apostrophes), so scan its lines.
    let offset = 0, labelled;
    for (const line of folded.split('\n')) {
      if (DATE_LABEL.test(line)) {
        labelled = dates.find(candidate => candidate.index >= offset && candidate.index < offset + line.length);
        if (labelled) break;
      }
      offset += line.length + 1;
    }
    date = labelled ?? dates[0];
    // A time stated right after that date on its line is checked, not required.
    const lineEnd = folded.indexOf('\n', date.end);
    const after = folded.slice(date.end, Math.min(lineEnd < 0 ? folded.length : lineEnd, date.end + 40));
    const next = timesIn(after)[0];
    if (next) time = next.minutes[0];
  }
  const numbers = date ? [] : numbersIn(folded.replace(TIME_12, ' ').replace(TIME_24, ' ')
    .replace(/^\s*(?:[-*+]|\d{1,3}[.)])\s+/gm, ' ').replace(/\b(?:event|evento|item)\s+\d{1,3}\b/g, ' '));
  const facts = date ? {date: {y: date.y, m: date.m, d: date.d}, ...(time === undefined ? {} : {time})}
    : numbers.length && numbers.length <= 6 ? {numbers} : {};
  const full = tokens.length > 0 && Boolean(facts.date || facts.numbers);
  return {tokens, ...facts, full};
}

function lineTable(text) {
  const lines = [];
  let start = 0;
  for (const text_ of text.split('\n')) {
    lines.push({start, end: start + text_.length, text: text_});
    start += text_.length + 1;
  }
  return lines;
}

// Candidate claim segments for one citation, smallest first: its sentence (in
// prose) or line, its enclosing list items, its block, then its section.
function segmentRanges(masked, lines, index) {
  const at = lines.findIndex(line => index >= line.start && index <= line.end);
  const line = lines[at];
  const ranges = [];
  if (TABLE_ROW.test(line.text)) return [[line.start, line.end]];
  const listLike = ITEM.test(line.text) || HEADING.test(line.text) || /^\s*(?:\*\*|__)?[^:\n]{1,40}[:：]/.test(line.text);
  if (!listLike) {
    // Prose: the sentence containing the citation. Abbreviations and initials
    // do not end a sentence.
    const text = masked.slice(line.start, line.end);
    const offset = index - line.start;
    let begin = 0, finish = text.length;
    for (const match of text.matchAll(/[.!?]+["')\]]*\s+(?=\S)/g)) {
      const word = text.slice(0, match.index).match(/(\S+)$/)?.[1] ?? '';
      if (/^(?:vs|st|jr|sr|dr|mr|mrs|ms|no|inc|ltd|co|ave|blvd|mt|ft|approx|e\.g|i\.e|[A-Za-z])$/i.test(word) ||
          EN_MONTHS[fold(word)] !== undefined) continue;
      const boundary = match.index + match[0].length;
      if (boundary <= offset) begin = boundary;
      else { finish = match.index + 1; break; }
    }
    ranges.push([line.start + begin, line.start + finish]);
  }
  ranges.push([line.start, line.end]);
  // Enclosing list items: each owner line with its whole subtree.
  const isItem = i => ITEM.test(lines[i].text);
  const stop = i => blank(lines[i]) || HEADING.test(lines[i].text) || RULE.test(lines[i].text);
  const subtree = owner => {
    let last = owner;
    for (let j = owner + 1; j < lines.length && !stop(j); j++) {
      if (isItem(j) && indentOf(lines[j].text) <= indentOf(lines[owner].text)) break;
      last = j;
    }
    return [lines[owner].start, lines[last].end];
  };
  let current = at;
  if (isItem(current)) ranges.push(subtree(current));
  for (let depth = 0; depth < 6; depth++) {
    let owner;
    for (let j = current - 1; j >= 0 && !stop(j); j--) {
      if (isItem(j) && (isItem(current) ? indentOf(lines[j].text) < indentOf(lines[current].text)
        : indentOf(lines[j].text) <= indentOf(lines[current].text))) { owner = j; break; }
    }
    if (owner === undefined) break;
    ranges.push(subtree(owner));
    current = owner;
  }
  // Block: contiguous lines between blank lines and rules; a heading on top
  // belongs to it, a heading below starts the next one.
  let top = at, bottom = at;
  while (top > 0 && !blank(lines[top - 1]) && !RULE.test(lines[top - 1].text) && !HEADING.test(lines[top].text)) top--;
  while (bottom + 1 < lines.length && !blank(lines[bottom + 1]) && !RULE.test(lines[bottom + 1].text) &&
    !HEADING.test(lines[bottom + 1].text)) bottom++;
  ranges.push([lines[top].start, lines[bottom].end]);
  // Section: from the nearest heading (or rule) above to the next one below.
  top = at; bottom = at;
  while (top > 0 && !HEADING.test(lines[top].text) && !RULE.test(lines[top - 1].text)) top--;
  while (bottom + 1 < lines.length && !HEADING.test(lines[bottom + 1].text) && !RULE.test(lines[bottom + 1].text)) bottom++;
  ranges.push([lines[top].start, lines[bottom].end]);
  return ranges.filter((range, i) => !ranges.slice(0, i).some(other => other[0] === range[0] && other[1] === range[1]));
}

// The claim anchors the answer attaches to each occurrence of `href`. An
// occurrence takes the smallest segment that yields a title and a date or
// number, never one that also covers a different citation. Occurrences without
// any anchor (a bare reference list) are omitted.
export function citationClaims(answer, href, {portuguese = false} = {}) {
  const text = String(answer ?? '');
  const key = citationKey(href);
  if (!key) return [];
  const spans = citationSpans(text);
  // Anchors never come from URLs: every link is blanked, offsets unchanged.
  let masked = text;
  for (const span of spans) masked = masked.slice(0, span.index) + ' '.repeat(span.raw.length) + masked.slice(span.index + span.raw.length);
  const lines = lineTable(masked);
  const claims = [];
  for (const span of spans) {
    if (span.key !== key) continue;
    const linkText = text.slice(Math.max(0, span.index - 220), span.index).match(/\[([^[\]\n]{1,200})\]\(\s*<?$/)?.[1];
    const ranges = segmentRanges(masked, lines, span.index);
    const within = range => new Set(spans.filter(other => other.key && other.index >= range[0] &&
      other.index < range[1]).map(other => other.key));
    const base = within(ranges[0]);
    const unitText = masked.slice(ranges[0][0], ranges[0][1]);
    let chosen;
    for (const range of ranges) {
      if ([...within(range)].some(other => !base.has(other))) break;
      const anchors = anchorsOf(masked.slice(range[0], range[1]), {portuguese, linkText, unitText});
      if (anchors.full) { chosen = anchors; break; }
      chosen ??= anchors;
    }
    if (chosen && (chosen.tokens.length || chosen.date || chosen.numbers)) claims.push(chosen);
  }
  return claims;
}

const NOT_FOUND = /\b(?:page not found|404 (?:error|not found)|error 404|not found \(404\)|(?:this|the) page (?:could not|cannot|cant|can not) be found|page (?:you requested|you are looking for|you were looking for) (?:could not|cannot|cant|does not|doesnt|is no longer)|(?:event|page) (?:is )?no longer available|no longer available|pagina nao encontrada|nao foi possivel encontrar)\b/;
const CANCELLED = /\b(?:cancell?ed|postponed|called off|cancelad[oa]|adiad[oa])\b/;
const escape = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

// Page text without anything URL-shaped, folded: a soft-404 that echoes the
// requested path or host cannot supply the anchors.
export function preparePageText(text) {
  return fold(String(text ?? '').slice(0, HOST_CITATION_LIMITS.maxPageChars)
    .replace(/\bhttps?:\/\/\S+/gi, ' ').replace(/\bwww\.\S+/gi, ' ')
    .replace(/\b(?:[a-z0-9-]+\.)+(?:com|net|org|edu|gov|io|co|us|uk|br|pt|info|biz|events?|tickets?)\b\S*/gi, ' ')
    .replace(/(?:^|\s)\/[\w.~%-]+(?:\/[\w.~%-]*)*/g, ' '))
    .replace(/\s+/g, ' ');
}

// Offsets where the whole title occurs: every token within CLUSTER characters
// of an occurrence of the first one.
const CLUSTER = 120;
function titleOffsets(page, patterns, from, to) {
  const [first, ...rest] = patterns;
  const offsets = [];
  const scan = new RegExp(first.source, 'g');
  scan.lastIndex = Math.max(0, from);
  for (let match; (match = scan.exec(page)) && match.index < to;) {
    const around = page.slice(Math.max(0, match.index - CLUSTER), match.index + CLUSTER);
    if (rest.every(pattern => pattern.test(around))) offsets.push(match.index);
  }
  return offsets;
}

function occurrenceSupported(page, pageDates, pageTimes, claim) {
  const W = HOST_CITATION_LIMITS.windowChars;
  const tokenPatterns = claim.tokens.map(token => new RegExp(`\\b${escape(token)}\\b`));
  const windowHolds = (from, to) => {
    const window = page.slice(Math.max(0, from), to);
    return tokenPatterns.every(pattern => pattern.test(window)) && !CANCELLED.test(window);
  };
  if (claim.date) {
    for (const date of pageDates) {
      if (date.m !== claim.date.m || date.d !== claim.date.d) continue;
      if (claim.date.y !== undefined && date.y !== claim.date.y) continue;
      // The title must sit near this date with no other date between them,
      // so a listing's neighbouring row cannot lend its title or date.
      const paired = !tokenPatterns.length || titleOffsets(page, tokenPatterns, date.index - W, date.end + W).some(offset => {
        const [from, to] = offset < date.index ? [offset, date.index] : [date.end, offset];
        return !pageDates.some(other => other.index > from && other.index < to && (other.m !== date.m || other.d !== date.d)) &&
          !CANCELLED.test(page.slice(Math.min(offset, date.index), Math.max(offset, date.end) + 80));
      });
      if (!paired) continue;
      if (claim.time !== undefined) {
        // A stated time must not contradict the page: times right next to the
        // date must include it. A page showing no time there leaves it unchecked.
        const nearby = pageTimes.filter(time => time.index >= date.index - 80 && time.index <= date.end + 80);
        if (nearby.length && !nearby.some(time => time.minutes.includes(claim.time))) continue;
      }
      return true;
    }
    return false;
  }
  const numberPattern = number => new RegExp(`(?<![\\d.])${escape(number)}(?![\\d]|\\.\\d)`, 'g');
  const plain = page.replace(/(\d),(?=\d{3}\b)/g, '$1');
  if (claim.numbers?.length) {
    const [first, ...rest] = claim.numbers;
    for (const match of plain.matchAll(numberPattern(first))) {
      const from = match.index - W, to = match.index + first.length + W;
      if (windowHolds(from, to) && rest.every(number => numberPattern(number).test(plain.slice(Math.max(0, from), to)))) return true;
    }
    return false;
  }
  // Title only (a secondary mention): the whole title somewhere on the page.
  return !tokenPatterns.length || titleOffsets(page, tokenPatterns, 0, page.length).length > 0;
}

// Whether prepared page text supports every anchored claim for one URL. At
// least one claim must carry a title and a date or number.
export function pageSupportsClaims(pageText, claims) {
  if (!claims.some(claim => claim.full)) return {supported: false, reason: 'insufficient-anchors'};
  const page = preparePageText(pageText);
  if (NOT_FOUND.test(page)) return {supported: false, reason: 'not-found-page'};
  const pageDates = datesIn(page, {page: true});
  const pageTimes = timesIn(page, {page: true});
  for (const claim of claims) {
    if (!occurrenceSupported(page, pageDates, pageTimes, claim)) return {supported: false, reason: 'anchors-not-found'};
  }
  return {supported: true};
}

const sameSite = (a, b) => a.replace(/^www\./, '') === b.replace(/^www\./, '');

// A query string can be echoed into a search or error page; its terms are not
// independent evidence. The fragment is already dropped from citations.
function reflectedInQuery(href, claims) {
  const url = new URL(href);
  if (!url.search) return false;
  let query;
  try { query = fold(decodeURIComponent(url.search.replace(/\+/g, ' '))); } catch { return true; }
  const words = new Set(query.split(/[^a-z0-9]+/));
  return claims.some(claim => claim.tokens.some(token => words.has(token)) || (claim.numbers ?? []).some(number => words.has(number)) ||
    datesIn(query.replace(/[^a-z0-9]+/g, ' ')).some(date => claim.date && date.m === claim.date.m && date.d === claim.date.d));
}

// Web access for these reads follows the operator's configuration: disabled or
// denied page-reading tools (globally, for the agent, or in the sandbox tool
// allowlist) mean no host reads either.
export function citationPageReadsAllowed(config, agentId = 'pixel') {
  const web = ['web_fetch', 'pixel_ods_web_extract'];
  if (config?.tools?.web?.fetch?.enabled === false) return false;
  const agent = config?.agents?.list?.find?.(entry => entry?.id === agentId);
  for (const deny of [config?.tools?.deny, agent?.tools?.deny]) {
    if (Array.isArray(deny) && deny.some(item => [...web, 'group:web', '*'].includes(item))) return false;
  }
  // An explicit allowlist must admit both page-reading tools.
  for (const allow of [config?.tools?.allow, agent?.tools?.allow, config?.tools?.sandbox?.tools?.allow,
    agent?.tools?.sandbox?.tools?.allow]) {
    if (Array.isArray(allow) && allow.length && !allow.includes('*') && !allow.includes('group:web') &&
        !web.every(tool => allow.includes(tool))) return false;
  }
  return true;
}

export function createHostCitationVerifier({readPage, allowed = () => true, limits = {},
  now = () => performance.now(), setTimer = setTimeout, clearTimer = clearTimeout} = {}) {
  if (typeof readPage !== 'function') throw new TypeError('Pixel host citation verification needs the guarded page reader');
  const bounds = {...HOST_CITATION_LIMITS, ...limits};
  return {
    allowed: () => { try { return allowed() === true; } catch { return false; } },
    // Verifies every URL or reports why not. Never throws; never waits past
    // the time budget. `verified` lists only URLs whose page carried anchors.
    // An aborted `signal` (the owner cancelled the run) ends every read at
    // once and verifies nothing.
    async verify({answer, urls, portuguese = false, signal}) {
      const started = now();
      const result = (fields) => ({verified: [], results: [], fetched: 0, ...fields, elapsedMs: Math.round(now() - started)});
      if (signal?.aborted) return result({skipped: 'cancelled'});
      if (!Array.isArray(urls) || !urls.length) return result({skipped: 'no-candidates'});
      if (urls.length > bounds.maxUrls) return result({skipped: 'too-many-citations'});
      const claims = new Map(urls.map(url => [url, citationClaims(answer, url, {portuguese})]));
      // All or nothing: without anchors for every URL no read can avoid the
      // revision, so none is spent.
      if ([...claims.values()].some(list => !list.some(claim => claim.full))) return result({skipped: 'insufficient-anchors'});
      if (urls.some(url => reflectedInQuery(url, claims.get(url)))) return result({skipped: 'query-reflection'});
      // Parallel reads under one deadline. At the deadline every pending read
      // is aborted and counts as not read; finished reads keep their result.
      const controller = new AbortController();
      const settled = new Array(urls.length);
      let timer, stop;
      const deadline = new Promise(resolve => { timer = setTimer(resolve, bounds.budgetMs); });
      const cancelled = new Promise(resolve => { stop = resolve; });
      signal?.addEventListener('abort', stop, {once: true});
      const timeoutSeconds = Math.max(1, Math.ceil(bounds.budgetMs / 1000));
      const reads = urls.map((url, i) => Promise.resolve()
        .then(() => readPage(url, {signal: controller.signal, timeoutSeconds, types: PUBLIC_PAGE_TEXT_TYPES}))
        .catch(() => ({ok: false, reason: 'blocked'}))
        .then(page => { settled[i] ??= page; }));
      await Promise.race([Promise.all(reads), deadline, cancelled]);
      clearTimer(timer);
      signal?.removeEventListener('abort', stop);
      controller.abort();
      if (signal?.aborted) return result({skipped: 'cancelled', fetched: urls.length});
      const pages = urls.map((_, i) => settled[i] ??= {ok: false, reason: 'timeout'});
      const results = urls.map((url, i) => {
        const page = pages[i];
        const outcome = {url, ...(page.status ? {status: page.status} : {}), ...(page.finalUrl ? {finalUrl: page.finalUrl} : {})};
        if (!page.ok) return {...outcome, verified: false, reason: page.reason ?? 'unavailable'};
        const cited = new URL(url), final = new URL(page.finalUrl);
        // A redirect to another site or to the site root (a soft 404) is not
        // the cited page.
        if (!sameSite(cited.hostname, final.hostname) || (cited.pathname.length > 1 && final.pathname === '/')) {
          return {...outcome, verified: false, reason: 'redirected-elsewhere'};
        }
        const check = pageSupportsClaims(page.text, claims.get(url));
        return {...outcome, verified: check.supported, ...(check.supported ? {} : {reason: check.reason})};
      });
      return result({results, fetched: urls.length,
        verified: results.filter(entry => entry.verified).map(({url, finalUrl}) => ({url, finalUrl}))});
    },
  };
}
