// Owner-requested exact text for visual deliveries. Extract only text the
// owner's current message explicitly requires (a cued quotation or a counted
// list of names) and check it against the bytes of the exact published
// snapshot. This never blocks publication: a miss withholds the completion
// claim and gives one repair step. Precision over recall: anything ambiguous
// is skipped, never reported, and dynamic script content counts as present.
// A listed item name that is present, but whose card heading only contains it
// while the other listed items are exact headings, is reported the same way,
// as is a listed card name that is no heading at all beside such headings.
// File names the owner lists for a named, published directory are checked
// against the receipt's complete published path list.
import {createHash} from 'node:crypto';
import * as fs from 'node:fs';
import path from 'node:path';

const MAX_OWNER_CHARS = 12000;
export const MAX_REQUESTED_LITERALS = 12;
const MAX_LITERAL_CHARS = 120;
const MAX_ITEM_CHARS = 80;
const MAX_ITEM_WORDS = 8;
const MAX_FILE_BYTES = 4 * 1024 * 1024;
const MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024;
const MAX_ELEMENT_RAW = 2000;
const MAX_ELEMENT_TEXTS = 50000;
const MAX_HEADINGS = 500;
const MAX_REPORTED_HEADING_CHARS = 120;
const PATH_COMPONENT = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const HTML_FILE = /\.html?$/i;
const TEXT_FILE = /\.(?:html?|m?js|json|css|svg|txt|csv|tsv|md|markdown|map)$/i;

// JavaScript \b is ASCII-only; Portuguese words need Unicode-aware edges.
const B = '(?<![\\p{L}\\p{N}_])', E = '(?![\\p{L}\\p{N}_])';
const words = source => new RegExp(`${B}(?:${source})${E}`, 'iu');
const tail = source => new RegExp(`${B}(?:${source})\\s*[:\\-–—]?\\s*$`, 'iu');

export const canonicalText = value => String(value ?? '').normalize('NFKC')
  .replace(/[\u200B-\u200D\u2060\uFEFF\u00AD]/g, '')
  .replace(/[\u2018\u2019\u201A\u201B\u2032]/g, "'")
  .replace(/[\u201C-\u201F\u2033\u00AB\u00BB]/g, '"')
  .replace(/[\u2010-\u2015\u2212]/g, '-')
  .replace(/\s+/g, ' ').trim();
const folded = value => canonicalText(value).toLowerCase();
const relaxed = value => folded(value).replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, '');

// A quotation is a requirement only directly after one of these cues.
const EXACT_CUE = tail(String.raw`exactly(?:\s+(?:as|this|the\s+(?:text|string|words?|phrase)))?|verbatim|word[- ]for[- ]word|(?:the\s+)?exact\s+(?:text|wording|words|string|title|name|heading|label|phrase|copy|caption)|exatamente(?:\s+(?:como|assim))?|literalmente|ao\s+p[ée]\s+da\s+letra|(?:texto|t[íi]tulo|nome|r[óo]tulo|frase|legenda)\s+exat[oa]`);
const NAME_CUE = tail(String.raw`named|called|titled|entitled|labell?ed|captioned|headed|chamad[oa]s?|nomead[oa]s?|intitulad[oa]s?|rotulad[oa]s?|denominad[oa]s?`);
const SAYS_CUE = tail(String.raw`(?:that|which)\s+(?:says|reads|shows|displays)|saying|reading|with\s+(?:the\s+)?(?:visible\s+)?(?:text|title|heading|label|caption|name|words?|wording|copy)|que\s+diz|dizendo|com\s+os\s+dizeres|com\s+(?:o\s+)?(?:texto|t[íi]tulo|nome|r[óo]tulo|legenda)`);
const ELEMENT_CUE = tail(String.raw`(?:(?:page|document|browser|tab)\s+)?title|heading|headline|sub-?heading|subtitle|tagline|banner|footer|button|caption|badge|h[1-6]|(?:the\s+)?(?:visible\s+)?text|rodap[ée]|t[íi]tulo(?:\s+d[aoe]\s+(?:p[áa]gina|documento|aba))?|subt[íi]tulo|bot[ãa]o|legenda|faixa`);
const MODAL_CUE = tail(String.raw`(?:must|should|shall|will|needs?\s+to|has\s+to|have\s+to|deve(?:m|r[áa])?|precisa(?:m)?|tem\s+que|t[êe]m\s+que)\s+(?:be|read|say|show|display|ser|dizer|mostrar|exibir)`);
const CHANGE_CUE = new RegExp(`${B}(?:change|set|rename|update|altere|mude|renomeie|defina|troque)${E}[^\\n]{0,80}?\\s(?:to|para)\\s*[:]?\\s*$`, 'iu');
const EXACT_MODE = words(String.raw`exactly|verbatim|exact|exatamente|literalmente|exat[oa]`);

// The local text before the quotation must name something visible on a page
// and nothing that makes the quotation a file, code, reply or data value.
const VISIBLE = words(String.raw`titles?|headings?|headlines?|h[1-6]|buttons?|links?|footers?|banners?|labels?|captions?|taglines?|subtitles?|sub-?headings?|badges?|cards?|tabs?|menus?|nav(?:igation)?|sections?|hero|pages?|site|website|webpage|screen|text|copy|t[íi]tulos?|subt[íi]tulos?|bot[õo]es|bot[ãa]o|rodap[ée]s?|faixas?|r[óo]tulos?|legendas?|cart[õo]es|cart[ãa]o|abas?|se[çc][ãa]o|se[çc][õo]es|p[áa]ginas?|texto|tela`);
const NON_VISUAL = words(String.raw`files?|file\s*names?|filenames?|director(?:y|ies)|folders?|paths?|repo(?:sitory)?|branch(?:es)?|commits?|variables?|functions?|methods?|class(?:es|names?)?|ids?|selectors?|keys?|fields?|propert(?:y|ies)|attributes?|urls?|domains?|ports?|commands?|modules?|packages?|workspace|projects?|database|json|csv|tsv|yaml|headers?|api|endpoints?|stdout|stderr|console|logs?|output|outputs|reply|replies|respond|response|answer|message|chat|print|echo|returns?|arquivos?|pastas?|diret[óo]rios?|caminhos?|vari[áa]ve(?:l|is)|fun[çc](?:[ãa]o|[õo]es)|chaves?|campos?|projetos?|resposta|responda|mensagem|sa[íi]da|comandos?`);
const NEGATED = new RegExp(`${B}(?:not|never|avoid|without|instead\\s+of|rather\\s+than|no\\s+longer|n[ãa]o|nunca|sem|evite|em\\s+vez\\s+de|ao\\s+inv[ée]s\\s+de)${E}|n['’]t${E}`, 'iu');
// Example markers qualify a nearby quotation; "like"/"say" only an adjacent one.
const EXAMPLE_BEFORE = new RegExp(`${B}(?:e\\.g\\.|i\\.e\\.|for\\s+(?:example|instance)|such\\s+as|something\\s+like|similar\\s+to|maybe|perhaps|suggestions?|examples?|por\\s+exemplo|p\\.\\s?ex\\.|ex\\.|algo\\s+como|talvez|sugest[ãa]o|exemplos?)(?![\\p{L}\\p{N}_])[^"“«‘\\n]{0,40}$|${B}(?:like|say|tipo)\\s*[,:]?\\s*$`, 'iu');
const EXAMPLE_AFTER = new RegExp(`^\\s*[,(]?\\s*(?:or|ou|etc|e\\.g\\.|for\\s+example|por\\s+exemplo|something\\s+like\\s+that|algo\\s+assim)${E}`, 'iu');
// Text being replaced, removed or offered as an alternative is not required.
const RIVAL_BEFORE = new RegExp(`${B}(?:from|or|ou|old|previous|former|original|current|existing|antig[oa]|anterior|atual|replace|replacing|remove|removing|delete|deleting|drop|substitua|substituir|remova|remover|apague|apagar)(?:\\s+[^\\s"“«‘]+){0,2}\\s*[:,]?\\s*$`, 'iu');
const PAGE_TITLE = new RegExp(`${B}(?:(?:page|document|browser|tab|html)\\s+title|title\\s+(?:tag|element)|title\\s+of\\s+the\\s+(?:web\\s*)?(?:page|document)|t[íi]tulo\\s+d[aoe]s?\\s+(?:p[áa]gina|documento|aba|navegador)|title\\s+(?:and|&|e)\\s+(?:(?:one|the|an?|o|um)\\s+)?h1|h1\\s+(?:and|&|e)\\s+(?:(?:the|o)\\s+)?(?:page\\s+)?(?:title|t[íi]tulo)|t[íi]tulo\\s+e\\s+(?:(?:o|um)\\s+)?h1)${E}|<title>`, 'iu');
const MAIN_HEADING = new RegExp(`${B}(?:h1|(?:main|top[- ]level|primary)\\s+heading|t[íi]tulo\\s+principal)${E}|<h1>`, 'iu');

const QUOTATION = /"([^"\n]{1,160})"|“([^”"\n]{1,160})”|«\s?([^»\n]{1,160}?)\s?»|‘([^’\n]{1,160})’|(?<![\p{L}\p{N}])'([^'\n]{1,160})'(?![\p{L}\p{N}])/gu;

const COUNTS = {two:2,three:3,four:4,five:5,six:6,seven:7,eight:8,nine:9,ten:10,
  dois:2,duas:2,'três':3,tres:3,quatro:4,cinco:5,seis:6,sete:7,oito:8,nove:9,dez:10};
// Named content units only. Steps, options, features, sections and projects
// often describe work or structure rather than visible names; skip them.
const ITEM_NOUNS = String.raw`cards?|events?|items?|products?|plans?|tiers?|tabs?|buttons?|links?|entries|entry|categor(?:y|ies)|dish(?:es)?|courses?|speakers?|members?|testimonials?|services?|tracks?|workshops?|activit(?:y|ies)|posts?|articles?|stor(?:y|ies)|chapters?|headings?|headlines?|titles?|badges?|cart[õo]es|cart[ãa]o|eventos?|itens|item|produtos?|planos?|abas?|bot[õo]es|bot[ãa]o|categorias?|pratos?|palestrantes?|membros?|depoimentos?|servi[çc]os?|oficinas?|atividades?|artigos?|cap[íi]tulos?|t[íi]tulos?`;
const ENUMERATION = new RegExp(`${B}(${Object.keys(COUNTS).join('|')}|[2-9]|1[0-2])${E}\\s+((?:[\\p{L}-]+\\s+){0,3}?)(${ITEM_NOUNS})${E}((?:\\s+[\\p{L}-]+){0,3}?)\\s*:\\s*([^\\n.;!?]{1,600})`, 'giu');
const ITEM_DESCRIPTION = words(String.raw`or|ou|etc|that|which|with|showing|containing|featuring|including|que|com|mostrando|contendo|incluindo`);
const ITEM_ARTICLE = /^(?:a|an|one|some|um|uma|uns|umas)\s/i;
// Lists of cards or headings ("three event cards") name items that each carry
// a heading; words after the noun ("tabs with titles") do not.
const HEADED_ITEMS = words(String.raw`cards?|headings?|headlines?|titles?|cart[õo]es|cart[ãa]o|t[íi]tulos?`);

// "create a public directory with index.html, ..." or "publish a folder
// containing ...": a named directory, or the publication itself.
const FILE_LIST_CUE = new RegExp(`${B}(?:(create|make|build|prepare|generate|produce|add|set\\s+up)|publish(?:\\s+only)?)` +
  `\\s+(?:(?:a|an|the)\\s+)?(?:([A-Za-z0-9][\\w.-]{0,63})\\/?\\s+)?(directory|folder|site|preview)` +
  `(?:\\s+(?:named|called)\\s+["'“‘]?([A-Za-z0-9][\\w.-]{0,63})["'”’]?\\/?)?` +
  `\\s+(with|containing|that\\s+contains)\\s*:?\\s+`, 'giu');
// Suffixes the host preview publishes (host/workspace_preview.py
// ALLOWED_SUFFIXES; a contract test keeps them equal). Any other file type
// makes the whole snapshot unpublishable, so it can never be required.
export const PREVIEW_FILE_SUFFIXES = Object.freeze(['.html', '.htm', '.css', '.js', '.mjs', '.json', '.svg', '.png', '.jpg',
  '.jpeg', '.gif', '.webp', '.ico', '.woff', '.woff2', '.ttf', '.txt', '.map', '.csv', '.tsv', '.md', '.markdown']);
// Detection is broader than publication so that a listed source or document
// is recognised as a file (and skips its list) rather than read as prose.
const FILE_NAME = /(?<![\w.\/\\@:-])([A-Za-z0-9][\w-]*(?:\.[\w-]+)*\.(?:html?|css|m?js|json|svg|png|jpe?g|gif|webp|ico|woff2?|ttf|txt|map|csv|tsv|md|markdown|py|ipynb|sh|bash|ps1|bat|rb|go|rs|java|kt|php|pl|ts|tsx|jsx|sql|db|sqlite|toml|ini|cfg|conf|env|ya?ml|xml|pdf|docx?|xlsx?|pptx?|zip|gz|tgz|tar|log|lock|wasm|mp3|mp4|wav|webm|ogg|avif|bmp|tiff?|otf|eot|exe|bin))(?![\w\/\\@-]|\.[A-Za-z0-9])/gi;
const LIBRARY_NAME = /^(?:node|vue|next|nuxt|three|chart|d3|express|react|angular|svelte|alpine|p5|htmx|anime|gsap)\.js$/i;
const FILE_LEAD = new Set(['and', '&', 'plus', 'also', 'then', 'a', 'an', 'one', 'raw', 'byte-for-byte', 'exact', 'verbatim',
  'source', 'plain', 'text', 'json', 'html', 'csv', 'copies', 'copy', 'file', 'files', 'named', 'called']);
const FILE_FOLLOWING = /^(?:the\s+)?following(?:\s+files)?\s*:?\s*/i;
const FILE_DROPPED = words(String.raw`removed|deleted|excluded|omitted|dropped|ignored|except|excluding|removing|deleting|minus`);
// Optional or conditional requests are not requirements.
const FILE_OPTIONAL = words(String.raw`optional(?:ly)?|if|unless|maybe|perhaps|possibly|ideally|preferably|may|might|could|(?:you|we)\s+can|nice\s+to\s+have|bonus|feel\s+free|opcional(?:mente)?|talvez|caso|se\s+poss[íi]vel`);
// "the sales.csv data as a chart" or "export.csv the page generates" names
// content or a runtime result, not a published file.
const FILE_CONTENT = new RegExp(`^\\s*(?:contents?|data|output|rows|values|records)${E}|${B}(?:generat\\w*|client-side|runtime|when\\s+clicked|on\\s+(?:click|demand)|download\\w*|export(?:s|ed|ing)?)${E}`, 'iu');

function ownerProse(text, {keepInlineCode = false} = {}) {
  // Code, fenced examples and block quotes never state visible requirements.
  return String(text ?? '').slice(0, MAX_OWNER_CHARS)
    .replace(/```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)/g, '\n')
    .replace(/`([^`\n]*)`/g, keepInlineCode ? '$1' : ' ')
    .replace(/^[ \t]*>[^\n]*/gm, '\n');
}

function literalShape(value) {
  const text = canonicalText(value);
  if (!text || Array.from(text).length > MAX_LITERAL_CHARS || !/[\p{L}\p{N}]/u.test(text)) return undefined;
  // Paths, file names, URLs, selectors and code identifiers are not page copy.
  if (/[/\\{}<>=;]|:\/\/|^[.#@$]|^--?[A-Za-z]/.test(text) || /^[\w.-]+\.[A-Za-z0-9]{1,5}$/.test(text) ||
      /^[a-z]+(?:[A-Z][a-z0-9]*)+$/.test(text) || /^[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+$/.test(text) ||
      /^[a-z0-9]+(?:-[a-z0-9]+)+$/.test(text)) return undefined;
  return text;
}

function lastWords(text, count) {
  const parts = text.split(/\s+/);
  return parts.slice(Math.max(0, parts.length - count - 1)).join(' ');
}

// Sentence start within [from, to): an abbreviation such as "e.g." does not
// end the sentence that it qualifies.
function sentenceStart(prose, from, to) {
  const masked = prose.slice(from, to).replace(/(?:e\.g|i\.e|p\. ?ex|vs|etc|approx|aprox)\./gi, value => value.replace(/[.\s]/g, '_'));
  let start = 0;
  for (const match of masked.matchAll(/[.!?;]\s+|\n/g)) start = match.index + match[0].length;
  return from + start;
}

function quotedLiterals(prose) {
  const found = [];
  let previousEnd = 0;
  for (const match of prose.matchAll(QUOTATION)) {
    const value = match.slice(1).find(group => group !== undefined);
    const quoteStart = match.index, quoteEnd = match.index + match[0].length;
    const local = prose.slice(sentenceStart(prose, previousEnd, quoteStart), quoteStart);
    const lead = prose.slice(Math.max(0, quoteStart - 60), quoteStart);
    const after = prose.slice(quoteEnd, quoteEnd + 40);
    previousEnd = quoteEnd;
    const text = literalShape(value);
    if (!text) continue;
    const nearby = lastWords(local, 12);
    const exact = EXACT_CUE.test(local);
    const cued = exact || NAME_CUE.test(local) || SAYS_CUE.test(local) || ELEMENT_CUE.test(local) ||
      CHANGE_CUE.test(local) || (MODAL_CUE.test(local) && VISIBLE.test(nearby));
    if (!cued || !VISIBLE.test(nearby) || NON_VISUAL.test(lastWords(local, 10)) || NEGATED.test(local) ||
        EXAMPLE_BEFORE.test(lead) || EXAMPLE_AFTER.test(after) || RIVAL_BEFORE.test(local)) continue;
    const targets = [];
    if (PAGE_TITLE.test(local)) targets.push('page title');
    if (MAIN_HEADING.test(local)) targets.push('h1');
    found.push({text, match: exact || EXACT_MODE.test(lastWords(local, 3)) ? 'exact' : 'caseless', targets});
  }
  return found;
}

function enumerationItems(list, count) {
  if (ITEM_DESCRIPTION.test(list)) return undefined;
  let parts = list.split(',').map(part => part.trim());
  if (parts.some(part => !part)) return undefined;
  const conjunction = /^(?:and|e|&)\s+/i;
  const last = parts.at(-1);
  if (parts.length === count && count > 1 && conjunction.test(last)) {
    parts[parts.length - 1] = last.replace(conjunction, '');
  } else if (parts.length === count - 1) {
    const split = /^(.*\S)\s+(?:and|e|&)\s+(\S.*)$/iu.exec(last);
    if (!split) return undefined;
    parts = [...parts.slice(0, -1), split[1], split[2]];
  } else if (parts.length !== count) return undefined;
  const items = [];
  for (const part of parts) {
    const quoted = /^["“«‘'](.+)["”»’']$/u.exec(part);
    const value = (quoted ? quoted[1] : part).trim();
    const text = literalShape(value);
    if (!text || Array.from(text).length > MAX_ITEM_CHARS || text.split(' ').length > MAX_ITEM_WORDS ||
        /[()[\]:"“”«»]/u.test(text) || ITEM_ARTICLE.test(text) ||
        (!quoted && !/^[\p{Lu}\p{N}]/u.test(text))) return undefined;
    items.push(text);
  }
  return new Set(items.map(relaxed)).size === items.length ? items : undefined;
}

// Item literals carry the ordinal of their list: sibling-heading evidence for
// a name comes only from the other names of the same list.
function enumeratedLiterals(prose) {
  const found = [];
  let list = 0;
  for (const match of prose.matchAll(ENUMERATION)) {
    const count = COUNTS[match[1].toLowerCase()] ?? Number(match[1]);
    const prefix = prose.slice(sentenceStart(prose, 0, match.index), match.index);
    if (NEGATED.test(prefix) || EXAMPLE_BEFORE.test(prefix) || NON_VISUAL.test(`${match[2]} ${match[4]}`)) continue;
    const items = enumerationItems(match[5], count);
    // The noun phrase ends before a connector: "event cards", not "tabs with titles".
    const noun = `${match[2]}${match[3]}${match[4].replace(new RegExp(`\\s+(?:with|that|which|for|of|in|on|to|as|having|com|de|do|da|para|que|em)${E}[\\s\\S]*$`, 'iu'), '')}`;
    const targets = HEADED_ITEMS.test(noun) ? ['heading'] : [];
    if (items) found.push(...items.map(text => ({text, match: 'item', targets, list})));
    if (items) list += 1;
  }
  return found;
}

// Each comma or "and" part is either [lead words] one publishable file name
// [description without another file name], or continues the previous part's
// description. Anything else (a nested list, "copy of x.py", a path, a file
// type the host cannot publish, an optional, removed or runtime-made file)
// skips the whole list.
function fileListItems(clause) {
  if (FILE_OPTIONAL.test(clause)) return undefined;
  const items = [];
  for (const part of clause.split(/,|\s+(?:and|&|plus)\s+/i)) {
    const segment = part.replace(/["'“”‘’«»]/g, ' ').trim();
    const names = [...segment.matchAll(FILE_NAME)].filter(name => !LIBRARY_NAME.test(name[1]));
    if (!names.length) {
      if (!items.length || /[\/\\]/.test(segment)) return undefined;
      continue;
    }
    const lead = segment.slice(0, names[0].index).replace(FILE_FOLLOWING, '').replace(/:\s*$/, '').split(/\s+/).filter(Boolean);
    const description = segment.slice(names[0].index + names[0][0].length);
    if (names.length > 1 || /[\/\\]/.test(segment) || !lead.every(word => FILE_LEAD.has(word.toLowerCase())) ||
        NEGATED.test(description) || FILE_DROPPED.test(description) || FILE_CONTENT.test(description) ||
        !PREVIEW_FILE_SUFFIXES.includes(path.extname(names[0][1]).toLowerCase())) return undefined;
    items.push(names[0][1]);
  }
  return items.length ? items : undefined;
}

function fileLiterals(prose) {
  const found = [];
  for (const match of prose.matchAll(FILE_LIST_CUE)) {
    const prefix = prose.slice(sentenceStart(prose, 0, match.index), match.index);
    // A created directory must be named so that it binds to the published
    // one; "publish a (static) site containing" is the publication itself
    // ("publish the site with x.js removed" is not a list of contents).
    const folder = /^(?:directory|folder)$/i.test(match[3]);
    const directory = match[4] ?? (folder ? match[2] : undefined) ?? '';
    if (NEGATED.test(prefix) || EXAMPLE_BEFORE.test(prefix) || FILE_OPTIONAL.test(prefix) ||
        (match[1] ? !folder || !directory : /^with$/i.test(match[5]))) continue;
    const start = match.index + match[0].length;
    const end = prose.slice(start).search(/[.!?;](?=\s|$)|\n/);
    const items = fileListItems(prose.slice(start, end < 0 ? prose.length : start + end));
    if (items) found.push(...items.map(text => ({text, match: 'file', targets: [], directory})));
  }
  return found;
}

// Returns only literals the current owner message explicitly requires.
export function extractRequestedLiterals(ownerText) {
  const prose = ownerProse(ownerText);
  const seen = new Set(), literals = [];
  for (const literal of [...quotedLiterals(prose), ...enumeratedLiterals(prose),
    ...fileLiterals(ownerProse(ownerText, {keepInlineCode: true}))]) {
    const key = relaxed(literal.text);
    if (!key || seen.has(key) || literals.length >= MAX_REQUESTED_LITERALS) continue;
    seen.add(key);
    literals.push(Object.freeze({...literal, targets: Object.freeze(literal.targets)}));
  }
  return Object.freeze(literals);
}

const NAMED_ENTITIES = {amp:'&',lt:'<',gt:'>',quot:'"',apos:"'",nbsp:'\u00a0',copy:'©',reg:'®',trade:'™',
  mdash:'—',ndash:'–',hellip:'…',lsquo:'‘',rsquo:'’',ldquo:'“',rdquo:'”',laquo:'«',raquo:'»',middot:'·',bull:'•',
  times:'×',deg:'°',euro:'€',pound:'£',cent:'¢',sect:'§',iexcl:'¡',iquest:'¿',szlig:'ß',
  aacute:'á',Aacute:'Á',agrave:'à',Agrave:'À',acirc:'â',Acirc:'Â',atilde:'ã',Atilde:'Ã',auml:'ä',Auml:'Ä',
  ccedil:'ç',Ccedil:'Ç',eacute:'é',Eacute:'É',egrave:'è',Egrave:'È',ecirc:'ê',Ecirc:'Ê',euml:'ë',
  iacute:'í',Iacute:'Í',icirc:'î',oacute:'ó',Oacute:'Ó',ocirc:'ô',Ocirc:'Ô',otilde:'õ',Otilde:'Õ',ouml:'ö',Ouml:'Ö',
  uacute:'ú',Uacute:'Ú',ucirc:'û',uuml:'ü',Uuml:'Ü',ntilde:'ñ',Ntilde:'Ñ'};
const decodeEntities = value => value.replace(/&(?:#(\d{1,7})|#[xX]([0-9a-fA-F]{1,6})|([A-Za-z][A-Za-z0-9]{1,31}));?/g,
  (whole, decimal, hex, name) => {
    if (decimal || hex) {
      const code = Number.parseInt(decimal ?? hex, decimal ? 10 : 16);
      return code > 0 && code <= 0x10FFFF && (code < 0xD800 || code > 0xDFFF) ? String.fromCodePoint(code) : whole;
    }
    return Object.hasOwn(NAMED_ENTITIES, name) ? NAMED_ENTITIES[name] : whole;
  });
const unescapeScript = value => value.replace(/\\u\{([0-9a-fA-F]{1,6})\}|\\u([0-9a-fA-F]{4})|\\x([0-9a-fA-F]{2})|\\([\s\S])/g,
  (whole, braced, unicode, hex, other) => {
    const code = braced ?? unicode ?? hex;
    if (code) { const point = Number.parseInt(code, 16); return point <= 0x10FFFF ? String.fromCodePoint(point) : whole; }
    return /[nrtfv]/.test(other) ? ' ' : other;
  });

// Comments are not rendered. Remove CSS comments and whole-line script
// comments only; a trailing script comment is kept rather than risk treating
// string or regular-expression text as a comment.
const withoutComments = (source, kind) => kind === 'style'
  ? source.replace(/\/\*[\s\S]*?(?:\*\/|(?![\s\S]))/g, ' ')
  : source.replace(/^[ \t]*\/\*[\s\S]*?(?:\*\/|(?![\s\S]))/gm, ' ').replace(/^[ \t]*\/\/[^\n]*/gm, ' ');

const VOID_ELEMENTS = new Set(['area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr']);
const TEXT_ATTRIBUTE = /^(?:aria-label|title|alt|placeholder|value|label|data-[\w-]+)$/i;

// A bounded static reading of authored HTML: element and text-node strings,
// titles, h1s and labelling attributes. Inline scripts and styles are opaque.
function readHtml(source, corpus) {
  const html = source.replace(/<!--[\s\S]*?(?:-->|$)/g, '');
  const tag = /<(\/?)([A-Za-z][A-Za-z0-9-]*)((?:[^>"']|"[^"]*"|'[^']*')*)>/g;
  const stack = [];
  let joined = '', spaced = '', at = 0, match;
  const record = raw => {
    if (raw.length > MAX_ELEMENT_RAW || corpus.elements.length >= MAX_ELEMENT_TEXTS) return undefined;
    const value = canonicalText(raw);
    if (value) corpus.elements.push(value);
    return value;
  };
  const text = raw => {
    const value = decodeEntities(raw);
    joined += value; spaced += value;
    record(value);
  };
  const close = depth => {
    while (stack.length > depth) {
      const element = stack.pop();
      const values = [record(joined.slice(element.joined)), record(spaced.slice(element.spaced))].filter(Boolean);
      if (element.name === 'title') corpus.titles.push(...values);
      if (element.name === 'h1') corpus.h1s.push(...values);
      if (/^h[1-6]$/.test(element.name) && values.length && corpus.headings.length <= MAX_HEADINGS) {
        corpus.headings.push({level: Number(element.name[1]), family: element.family, forms: values});
      }
    }
  };
  while ((match = tag.exec(html))) {
    text(html.slice(at, match.index));
    spaced += ' ';
    at = tag.lastIndex;
    const name = match[2].toLowerCase();
    if (match[1]) {
      const depth = stack.map(element => element.name).lastIndexOf(name);
      if (depth >= 0) close(depth);
      continue;
    }
    let classes = '';
    for (const attribute of match[3].matchAll(/([^\s=/"'>]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))/g)) {
      if (/^class$/i.test(attribute[1])) classes = attribute[2] ?? attribute[3] ?? attribute[4] ?? '';
      if (!TEXT_ATTRIBUTE.test(attribute[1])) continue;
      const value = decodeEntities(attribute[2] ?? attribute[3] ?? attribute[4] ?? '');
      corpus.attributes.push(value);
      record(value);
    }
    if (name === 'script' || name === 'style') {
      const end = html.slice(at).search(new RegExp(`</${name}\\s*>`, 'i'));
      corpus.opaque.push(withoutComments(end < 0 ? html.slice(at) : html.slice(at, at + end), name));
      at = end < 0 ? html.length : at + end;
      tag.lastIndex = at;
      continue;
    }
    if (!VOID_ELEMENTS.has(name) && !/\/\s*$/.test(match[3]) && stack.length < 1024) {
      // Sibling card headings share their tag and class list.
      const family = `${name}.${classes.split(/\s+/).filter(Boolean).sort().join('.')}`;
      stack.push({name, joined: joined.length, spaced: spaced.length, family});
    }
  }
  text(html.slice(at));
  close(0);
  corpus.visible.push(joined, spaced);
}

function textCorpus(files) {
  const corpus = {visible: [], attributes: [], elements: [], titles: [], h1s: [], headings: [], opaque: []};
  for (const file of files) {
    if (HTML_FILE.test(file.path)) readHtml(file.text, corpus);
    else if (/\.m?js$/i.test(file.path)) corpus.opaque.push(withoutComments(file.text, 'script'));
    else if (/\.css$/i.test(file.path)) corpus.opaque.push(withoutComments(file.text, 'style'));
    else corpus.opaque.push(file.text);
  }
  corpus.opaque = corpus.opaque.flatMap(source => [source, decodeEntities(unescapeScript(source))]);
  const forms = new Map();
  const form = (key, make) => forms.get(key) ?? forms.set(key, make()).get(key);
  return {
    titles: corpus.titles, h1s: corpus.h1s,
    // Every hN element in document order, with its level and relaxed forms.
    headings: () => form('headings', () => corpus.headings.length > MAX_HEADINGS ? []
      : corpus.headings.map(({level, family, forms}) => ({level, family, forms, keys: forms.map(relaxed)}))),
    opaque: exact => form(`opaque:${exact}`, () => corpus.opaque.map(exact ? canonicalText : folded)),
    visible: exact => form(`visible:${exact}`, () => [...corpus.visible, ...corpus.attributes].map(exact ? canonicalText : folded)),
    elements: () => form('elements', () => new Set(corpus.elements.map(relaxed))),
  };
}

// Whole-word occurrence of a relaxed phrase inside relaxed text.
function containsPhrase(text, phrase) {
  for (let at = text.indexOf(phrase); phrase && at >= 0; at = text.indexOf(phrase, at + 1)) {
    if (!/[\p{L}\p{N}]$/u.test(text.slice(Math.max(0, at - 2), at)) &&
        !/^[\p{L}\p{N}]/u.test(text.slice(at + phrase.length, at + phrase.length + 2))) return true;
  }
  return false;
}

const reportedHeading = value => {
  const chars = Array.from(value);
  return chars.length > MAX_REPORTED_HEADING_CHARS ? `${chars.slice(0, MAX_REPORTED_HEADING_CHARS - 1).join('')}…` : value;
};

// A listed item that is on the page but is never a whole heading, while a
// heading of some level contains it. Reported only if another item of the
// same list is exactly a heading of that same level (the items are evidently
// titled by those headings) and the longer heading names no other listed
// item. Tower2 round 073: "Dawn jazz" was a badge; its h2 read "Dawn Jazz at
// the Rose Pavilion" beside h2 "River Lantern Walk".
function headingOnlyMatch(key, corpus, siblings, items) {
  const headings = corpus.headings();
  if (!headings.length || headings.some(heading => heading.keys.includes(key))) return undefined;
  const others = items.filter(other => other !== key), evidence = siblings.filter(other => other !== key);
  const levels = new Set(headings.filter(heading => heading.keys.some(value => evidence.includes(value)))
    .map(heading => heading.level));
  const match = headings.find(heading => levels.has(heading.level) &&
    heading.keys.some(value => containsPhrase(value, key)) &&
    !others.some(other => heading.keys.some(value => containsPhrase(value, other))));
  return match ? reportedHeading(match.forms.at(-1)) : undefined;
}

// A listed card name that no hN heading equals or contains, while at least
// two headings of one tag and class list include another item of the same
// list exactly (the cards are evidently titled by those headings). Only
// rendered heading text counts: comments, scripts and styles are never
// heading text. Tower2 round 082: "Dawn jazz" was a badge; its card's
// h2.event-title read "Sunrise Sessions" beside h2.event-title "River lantern walk".
function unheadedItem(key, corpus, siblings) {
  const headings = corpus.headings();
  if (headings.some(heading => heading.keys.some(value => containsPhrase(value, key)))) return false;
  const others = siblings.filter(other => other !== key);
  const families = new Map();
  for (const heading of headings) families.set(heading.family, [...families.get(heading.family) ?? [], heading]);
  return [...families.values()].some(family => family.length > 1 &&
    family.some(heading => heading.keys.some(value => others.includes(value))));
}

// Each miss is {} (absent), {target} (not the page title or h1), {heading}
// (a listed item only inside a longer heading) or {unheaded} (a listed card
// name that is no heading beside its sibling cards' headings).
function literalMisses(literal, corpus, items, siblings) {
  const exact = literal.match === 'exact';
  const needle = exact ? canonicalText(literal.text) : folded(literal.text);
  // Script, data and style bytes may render the text at runtime. That is not
  // verified presence, but it is not a verified miss either.
  if (!needle || corpus.opaque(exact).some(source => source.includes(needle))) return [];
  if (literal.match === 'item') {
    const key = relaxed(needle);
    if (!corpus.elements().has(key)) return [{}];
    if (siblings.length < 2) return [];
    const heading = headingOnlyMatch(key, corpus, siblings, items);
    if (heading) return [{heading}];
    return literal.targets.includes('heading') && unheadedItem(key, corpus, siblings) ? [{unheaded: true}] : [];
  }
  if (literal.targets.length) {
    const equals = value => exact ? value === needle : relaxed(value) === relaxed(needle);
    return literal.targets.filter(target => !(target === 'page title' ? corpus.titles : corpus.h1s).some(equals))
      .map(target => ({target}));
  }
  return corpus.visible(exact).some(value => value.includes(needle)) ? [] : [{}];
}

// Pure check over already snapshot-bound text files ({path, text}).
export function missingRequestedText(literals, files) {
  if (!Array.isArray(literals) || !literals.length || !Array.isArray(files)) return [];
  const corpus = textCorpus(files);
  const listed = literals.filter(literal => literal.match === 'item');
  const items = listed.map(literal => relaxed(literal.text));
  const missing = [];
  for (const literal of literals) {
    if (literal.match === 'file') continue;
    const siblings = listed.filter(other => other.list === literal.list).map(other => relaxed(other.text));
    for (const miss of literalMisses(literal, corpus, items, siblings)) {
      missing.push(Object.freeze({text: literal.text, ...miss}));
    }
  }
  return missing;
}

// Accept file bytes only if they reproduce the host's snapshot digest.
function boundSnapshot(preview, entries) {
  if (entries.length !== preview.files) return undefined;
  entries.sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0);
  const digest = createHash('sha256');
  let bytes = 0;
  for (const [name, data] of entries) {
    const encodedName = Buffer.from(name, 'utf8');
    const nameLength = Buffer.alloc(4), dataLength = Buffer.alloc(8);
    nameLength.writeUInt32BE(encodedName.length);
    dataLength.writeBigUInt64BE(BigInt(data.length));
    digest.update(nameLength).update(encodedName).update(dataLength).update(data);
    bytes += data.length;
  }
  if (bytes !== preview.bytes || digest.digest('hex') !== preview.sha256) return undefined;
  return entries.filter(([name]) => TEXT_FILE.test(name)).map(([name, data]) => ({path: name, text: data.toString('utf8')}));
}

function trackedSnapshot(preview, trackedContent) {
  if (!(trackedContent instanceof Map)) return undefined;
  const prefix = `${preview.relativeDirectory}/`;
  const entries = [...trackedContent]
    .filter(([name, content]) => typeof name === 'string' && name.startsWith(prefix) && typeof content === 'string')
    .map(([name, content]) => [name.slice(prefix.length), Buffer.from(content, 'utf8')]);
  return boundSnapshot(preview, entries);
}

function workspaceSnapshot(preview, receipt, workspaceRoot) {
  if (typeof workspaceRoot !== 'string' || !path.isAbsolute(workspaceRoot)) return undefined;
  const directory = preview.relativeDirectory.split('/');
  const names = Array.isArray(receipt?.publishedPaths) && receipt.publishedPathsOmitted === 0
    ? receipt.publishedPaths : preview.files === 1 ? ['index.html'] : undefined;
  if (!names || names.length !== preview.files || !directory.every(part => PATH_COMPONENT.test(part))) return undefined;
  const entries = [];
  let total = 0;
  for (const name of names) {
    if (typeof name !== 'string' || !name.split('/').every(part => PATH_COMPONENT.test(part))) return undefined;
    const file = path.join(workspaceRoot, ...directory, ...name.split('/'));
    const info = fs.lstatSync(file);
    if (!info.isFile() || info.size > MAX_FILE_BYTES || (total += info.size) > MAX_SNAPSHOT_BYTES) return undefined;
    entries.push([name, fs.readFileSync(file)]);
  }
  return boundSnapshot(preview, entries);
}

// Listed files absent from the receipt's complete published path list (any
// depth), for literals bound to this directory's name or to the publication.
// Undefined when no literal applies or the list is not complete.
function missingPublishedFiles(literals, preview, receipt) {
  const directory = preview.relativeDirectory.split('/').at(-1).toLowerCase();
  const wanted = literals.filter(literal => literal.match === 'file' &&
    (!literal.directory || literal.directory.toLowerCase() === directory));
  if (!wanted.length) return undefined;
  const paths = receipt?.sha256 === preview.sha256 && Array.isArray(receipt.publishedPaths) &&
    receipt.publishedPathsOmitted === 0 ? receipt.publishedPaths : preview.files === 1 ? ['index.html'] : undefined;
  if (!paths || paths.length !== preview.files || !paths.every(name => typeof name === 'string')) return undefined;
  const names = new Set(paths.map(name => name.split('/').at(-1).toLowerCase()));
  return wanted.filter(literal => !names.has(literal.text.toLowerCase())).map(literal => ({text: literal.text, file: true}));
}

// Checks requested literals against the exact published snapshot: current-run
// written bytes first, then workspace files, each accepted only when they
// reproduce the receipt's snapshot digest. Unbound input yields no result.
export function requestedTextCheck(literals, preview, {receipt, trackedContent, workspaceRoot} = {}) {
  try {
    if (!Array.isArray(literals) || !literals.length || !preview || !/^[a-f0-9]{64}$/.test(preview.sha256 ?? '') ||
        !Number.isSafeInteger(preview.files) || preview.files < 1 || preview.files > 128 ||
        typeof preview.relativeDirectory !== 'string') return undefined;
    const text = literals.filter(literal => literal.match !== 'file');
    const files = text.length ? trackedSnapshot(preview, trackedContent) ?? workspaceSnapshot(preview, receipt, workspaceRoot)
      : undefined;
    const published = missingPublishedFiles(literals, preview, receipt);
    if (!files && !published) return undefined;
    return Object.freeze({siteId: preview.siteId, sha256: preview.sha256,
      missing: Object.freeze([...files ? missingRequestedText(text, files) : [], ...published ?? []].map(Object.freeze))});
  } catch {
    return undefined;
  }
}

// A bounded static outline of the published entry page for choosing one stable
// inspection locator: each element's tag, id, classes and parent, headings'
// authored accessible names, the class names the published scripts add,
// remove or toggle, and the one heading whose text is the owner's phrase
// (headingIndex). Read from the same digest-bound bytes as requestedTextCheck.
// It names locators; it verifies nothing. Unbound or oversized pages yield
// undefined.
const MAX_OUTLINE_ELEMENTS = 4000, MAX_OUTLINE_NAME_CHARS = 120, MAX_TOGGLED_CLASSES = 64;
const CSS_IDENTIFIER = /^-?[A-Za-z_][\w-]*$/;
function htmlOutline(source) {
  const html = source.replace(/<!--[\s\S]*?(?:-->|$)/g, '');
  const tag = /<(\/?)([A-Za-z][A-Za-z0-9-]*)((?:[^>"']|"[^"]*"|'[^']*')*)>/g;
  const elements = [], stack = [], scripts = [];
  let at = 0, match;
  const text = raw => {
    const open = stack.filter(item => item.text !== undefined);
    if (open.length) { const value = decodeEntities(raw); for (const item of open) item.text += value; }
  };
  const close = depth => {
    while (stack.length > depth) {
      const item = stack.pop();
      if (item.text === undefined) continue;
      // Accessible name as authored: aria-label, else the collapsed text.
      // aria-labelledby, or a name a locator cannot carry, leaves it unknown.
      const element = elements[item.index], label = item.label?.trim() ? item.label : item.text;
      const name = label.replace(/[\t\n\f\r ]+/g, ' ').trim();
      element.text = canonicalText(item.text);
      if (!item.labelledBy && name && Array.from(name).length <= MAX_OUTLINE_NAME_CHARS && !/[\p{C}\u2028\u2029]/u.test(name)) element.name = name;
    }
  };
  while ((match = tag.exec(html))) {
    text(html.slice(at, match.index));
    at = tag.lastIndex;
    const name = match[2].toLowerCase();
    if (match[1]) {
      const depth = stack.map(item => item.name).lastIndexOf(name);
      if (depth >= 0) close(depth);
      continue;
    }
    if (elements.length >= MAX_OUTLINE_ELEMENTS) return undefined;
    const attributes = new Map();
    for (const attribute of match[3].matchAll(/([^\s=/"'>]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))/g)) {
      const key = attribute[1].toLowerCase();
      if (!attributes.has(key)) attributes.set(key, decodeEntities(attribute[2] ?? attribute[3] ?? attribute[4] ?? ''));
    }
    const index = elements.push({tag: name, parent: stack.at(-1)?.index ?? -1,
      ...(attributes.has('id') ? {id: attributes.get('id')} : {}),
      classes: (attributes.get('class') ?? '').split(/[\t\n\f\r ]+/).filter(Boolean)}) - 1;
    if (name === 'script' || name === 'style') {
      const end = html.slice(at).search(new RegExp(`</${name}\\s*>`, 'i'));
      if (name === 'script') scripts.push(end < 0 ? html.slice(at) : html.slice(at, at + end));
      at = end < 0 ? html.length : at + end;
      tag.lastIndex = at;
      continue;
    }
    if (!VOID_ELEMENTS.has(name) && !/\/\s*$/.test(match[3]) && stack.length < 1024) {
      const heading = /^h[1-6]$/.test(name);
      if (heading) elements[index].heading = true;
      stack.push({name, index, ...(heading ? {text: '', label: attributes.get('aria-label'),
        labelledBy: attributes.has('aria-labelledby')} : {})});
    }
  }
  text(html.slice(at));
  close(0);
  return {elements, scripts};
}

// Class names a published script adds, removes, toggles or replaces by literal.
function toggledClasses(sources) {
  const found = new Set();
  for (const source of sources) {
    for (const call of withoutComments(source, 'script').matchAll(
      /\bclassList\s*\.\s*(?:add|remove|toggle|replace)\s*\(([^)]{0,300})\)|\.(?:addClass|removeClass|toggleClass)\s*\(([^)]{0,300})\)/g)) {
      for (const quoted of (call[1] ?? call[2]).matchAll(/(["'`])([^"'`]{1,200})\1/g)) {
        for (const value of quoted[2].split(/\s+/)) {
          if (CSS_IDENTIFIER.test(value) && found.size < MAX_TOGGLED_CLASSES) found.add(value);
        }
      }
    }
  }
  return [...found];
}

export function publishedElementOutline(phrase, preview, {receipt, trackedContent, workspaceRoot} = {}) {
  try {
    if (!preview || !/^[a-f0-9]{64}$/.test(preview.sha256 ?? '') || !Number.isSafeInteger(preview.files) ||
        preview.files < 1 || preview.files > 128 || typeof preview.relativeDirectory !== 'string') return undefined;
    const files = trackedSnapshot(preview, trackedContent) ?? workspaceSnapshot(preview, receipt, workspaceRoot);
    const entry = files?.find(file => file.path === 'index.html');
    const outline = entry && htmlOutline(entry.text);
    if (!outline) return undefined;
    const key = relaxed(phrase ?? '');
    const headings = key ? outline.elements.flatMap((element, index) =>
      element.heading && relaxed(element.text) === key ? [index] : []) : [];
    const stateClasses = toggledClasses([...outline.scripts, ...files.filter(file => /\.m?js$/i.test(file.path)).map(file => file.text)]);
    return Object.freeze({
      elements: Object.freeze(outline.elements.map(({text, ...element}) => Object.freeze({...element, classes: Object.freeze(element.classes)}))),
      stateClasses: Object.freeze(stateClasses), ...(headings.length === 1 ? {headingIndex: headings[0]} : {})});
  } catch {
    return undefined;
  }
}

const absent = check => check.missing.filter(miss => !miss.heading && !miss.unheaded && !miss.file);
const inHeadings = check => check.missing.filter(miss => miss.heading);
const unheaded = check => check.missing.filter(miss => miss.unheaded);
const unpublished = check => check.missing.filter(miss => miss.file);
const missingList = misses => misses
  .map(miss => JSON.stringify(miss.text) + (miss.target ? ` (${miss.target})` : '')).join(', ');
const nameList = misses => JSON.stringify([...new Set(misses.map(miss => miss.text))]);
const boundMisses = (preview, check) => Boolean(check?.missing?.length && preview &&
  check.siteId === preview.siteId && check.sha256 === preview.sha256);
const sentences = parts => parts.filter(Boolean).join(' ');

// Byte-stable apart from the quoted owner literals, fixed target labels and
// the snapshot's own heading text and directory, so per-slot coaching dedupe applies.
export function requestedTextInstruction(preview, check) {
  if (!boundMisses(preview, check)) return undefined;
  const missing = absent(check), names = unheaded(check), files = unpublished(check);
  return sentences([
    missing.length && `Requested text not found: ${missingList(missing)}. Use the owner's exact wording, republish and re-inspect.`,
    ...inHeadings(check).map(miss => `${JSON.stringify(miss.text)} appears only inside a longer heading ` +
      `(${JSON.stringify(miss.heading)}); use the exact name as the heading, then republish and re-inspect.`),
    names.length && `Requested names are on the page but not as headings, while other listed items are: ${nameList(names)}. ` +
      'Use each exact name as the heading of its item, then republish and re-inspect.',
    files.length && `Requested files are not in the published directory ${JSON.stringify(preview.relativeDirectory)}: ` +
      `${nameList(files)}. Put each one inside that directory, then republish it.`,
  ]);
}

// The one bounded revision for a snapshot that still lacks requested text:
// fixed text around a JSON list of the missing literals (each listed once).
export const REQUESTED_TEXT_REVISION_INSTRUCTION = [
  'Requested text is still missing from the published page: ',
  '. Add the exact text as requested (for example, as the card heading if the owner described it as a card title, ' +
  'or as the page title or h1 if the owner named them), republish with pixel_ods_workspace_preview, ' +
  'and keep everything else unchanged.',
];

// The same bounded revision for listed names that are only inside longer headings.
export const REQUESTED_HEADING_REVISION_INSTRUCTION = [
  'Requested names still appear only inside longer headings on the published page: ',
  '. Use each exact name as the whole heading of its item and put extra detail in body text, ' +
  'republish with pixel_ods_workspace_preview, and keep everything else unchanged.',
];

// The same for listed card names that are not headings at all.
export const REQUESTED_ITEM_HEADING_REVISION_INSTRUCTION = [
  'Requested names are still not headings on the published page: ',
  '. Use each exact name as the whole heading of its item, with the same heading element as the other listed items, ' +
  'put extra detail in body text, republish with pixel_ods_workspace_preview, and keep everything else unchanged.',
];

// The same for owner-listed files missing from the published directory.
export const REQUESTED_FILE_REVISION_INSTRUCTION = [
  'Requested files are still missing from the published directory: ',
  '. Put each file inside the directory you published (copy an existing file there with one short command ' +
  'instead of retyping it), republish that directory with pixel_ods_workspace_preview, and keep everything else unchanged.',
];

export function requestedTextRevisionInstruction(preview, check) {
  if (!boundMisses(preview, check)) return undefined;
  const missing = absent(check), headings = inHeadings(check), names = unheaded(check), files = unpublished(check);
  return sentences([
    missing.length && REQUESTED_TEXT_REVISION_INSTRUCTION.join(nameList(missing)),
    headings.length && REQUESTED_HEADING_REVISION_INSTRUCTION.join(nameList(headings)),
    names.length && REQUESTED_ITEM_HEADING_REVISION_INSTRUCTION.join(nameList(names)),
    files.length && REQUESTED_FILE_REVISION_INSTRUCTION.join(nameList(files)),
  ]);
}

export function requestedTextDeliveryNote(preview, check) {
  if (!boundMisses(preview, check)) return undefined;
  const missing = absent(check), headings = inHeadings(check), names = unheaded(check), files = unpublished(check);
  return sentences([
    missing.length && `The published page does not contain text the owner requested: ${missingList(missing)}.`,
    headings.length && 'The published page uses requested names only inside longer headings: ' +
      `${headings.map(miss => `${JSON.stringify(miss.text)} (${JSON.stringify(miss.heading)})`).join(', ')}.`,
    names.length && `The published page shows requested names, but not as headings like the other listed items: ${missingList(names)}.`,
    files.length && `The published directory does not contain files the owner requested: ${missingList(files)}.`,
    'The preview is available, but that requirement is not met.',
  ]);
}
