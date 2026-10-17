// Schema.org facts a page publishes in its markup, for the shared public-page
// reader (web-extract.mjs).
//
// Event pages and shop pages often build their visible text with JavaScript,
// which the reader does not run, while the served HTML already carries the
// event's date and venue or the offer's price and stock state as schema.org
// data: JSON-LD scripts, or microdata attributes such as <meta
// itemprop="price" content="549.99">. This module reads that data from the
// HTML the reader already fetched. It never runs a script: JSON-LD is parsed
// as JSON, microdata is read from tag attributes and text.
//
// Only whitelisted types and fields are kept, each value is plain text of
// bounded length, and the whole block is bounded:
// - Event and its subtypes: name, start and end date, status (only when not
//   scheduled), location (place name and postal address), organizer, url;
// - Product: name, sku, gtin, offers;
// - Offer and AggregateOffer: price (or low-high), currency, availability,
//   item condition, seller, priceValidUntil;
// - Organization and some subtypes: name, postal address, url, only when it
//   states an address and is not credited as a publisher, author, brand...
//   (those restate the site or a maker); organizers and sellers are shown by
//   name on their event or offer.
// Descriptions, reviews, images, performers' bios and every other field are
// never read. The block is page content: the reader puts it in the page text,
// inside the same untrusted-content boundary as the visible text, and says
// that it is markup data, not visible text.
//
// Everything here is linear in the size of the (already bounded) response.

export const STRUCTURED_DATA_LIMITS = Object.freeze({
  maxScripts: 16,          // JSON-LD scripts parsed per page
  maxScriptChars: 262_144, // a larger JSON-LD script is skipped
  maxJsonChars: 524_288,   // all JSON-LD parsed per page
  maxNodes: 4_000,         // JSON objects visited per page
  maxDepth: 12,
  maxItems: 8,             // events, products and offers shown
  maxOrganizations: 2,
  maxOffers: 3,            // per product
  maxValueChars: 160,
  maxUrlChars: 300,
  maxLineChars: 700,
  maxBlockChars: 2_400,
  maxTags: 60_000,         // tags scanned for microdata
  maxItemTags: 4_000,      // of those, tags with microdata attributes
  maxTagChars: 4_096,      // attribute text read per tag
  maxStack: 512,           // open elements tracked for microdata
  maxCaptureChars: 600,    // text read for one microdata property
});

const L = STRUCTURED_DATA_LIMITS;

export const STRUCTURED_DATA_HEADER =
  "Structured data published in this page's markup (schema.org; not part of the visible page text):";

const EVENT_TYPES = new Set(['Event', 'BusinessEvent', 'ChildrensEvent', 'ComedyEvent', 'CourseInstance',
  'DanceEvent', 'DeliveryEvent', 'EducationEvent', 'EventSeries', 'ExhibitionEvent', 'Festival', 'FoodEvent',
  'Hackathon', 'LiteraryEvent', 'MusicEvent', 'PublicationEvent', 'BroadcastEvent', 'OnDemandEvent', 'SaleEvent',
  'ScreeningEvent', 'SocialEvent', 'SportsEvent', 'TheaterEvent', 'VisualArtsEvent']);
const PRODUCT_TYPES = new Set(['Product', 'IndividualProduct', 'ProductModel', 'ProductGroup', 'SomeProducts']);
const OFFER_TYPES = new Set(['Offer', 'AggregateOffer']);
const ORGANIZATION_TYPES = new Set(['Organization', 'Corporation', 'LocalBusiness', 'Store', 'OnlineStore',
  'ElectronicsStore', 'ComputerStore', 'NGO', 'GovernmentOrganization', 'EducationalOrganization',
  'PerformingGroup', 'MusicGroup', 'TheaterGroup', 'DanceGroup', 'SportsOrganization', 'SportsTeam',
  'EntertainmentBusiness', 'NightClub', 'ComedyClub', 'MovieTheater', 'ArtGallery']);

// Where an organization is credited for a work rather than being its subject.
const CREDIT_KEYS = new Set(['publisher', 'author', 'creator', 'copyrightHolder', 'provider', 'sourceOrganization',
  'funder', 'sponsor', 'producer', 'contributor', 'editor', 'maintainer', 'brand', 'manufacturer',
  'parentOrganization', 'subOrganization', 'memberOf', 'worksFor', 'affiliation', 'isPartOf']);

// Enumeration members, in words. Anything else is not shown.
const EVENT_STATUS = {EventCancelled: 'cancelled', EventPostponed: 'postponed', EventRescheduled: 'rescheduled',
  EventMovedOnline: 'moved online'};
const AVAILABILITY = {InStock: 'in stock', OutOfStock: 'out of stock', SoldOut: 'sold out', PreOrder: 'pre-order',
  PreSale: 'pre-sale', BackOrder: 'back order', Discontinued: 'discontinued', LimitedAvailability: 'limited availability',
  OnlineOnly: 'online only', InStoreOnly: 'in store only', MadeToOrder: 'made to order', Reserved: 'reserved'};
const CONDITION = {NewCondition: 'new', UsedCondition: 'used', RefurbishedCondition: 'refurbished',
  DamagedCondition: 'damaged'};

const SCHEMA_PREFIX = /^(?:https?:\/\/(?:www\.)?schema\.org\/|schema:)/i;
// A schema.org @context: the vocabulary IRI or its published context document.
const SCHEMA_CONTEXT = /^https?:\/\/(?:www\.)?schema\.org(?:\/(?:docs\/jsonldcontext\.jsonld?)?)?$/i;
// C0/C1 controls, zero-width and bidirectional formatting characters.
const INVISIBLE = /[\u0000-\u001f\u007f-\u009f\u00ad\u200b-\u200f\u2028-\u202e\u2060-\u2069\ufeff]/g;
const NAMED_ENTITIES = {amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' '};

const record = value => Boolean(value) && typeof value === 'object' && !Array.isArray(value);
const own = (node, key) => (record(node) && Object.hasOwn(node, key) ? node[key] : undefined);
const cap = (text, max) => (text.length > max ? `${text.slice(0, max - 1).trimEnd()}\u2026` : text);

function decodeEntities(text) {
  return text.replace(/&(?:#x([0-9a-f]{1,6})|#(\d{1,7})|(amp|lt|gt|quot|apos|nbsp));/gi, (whole, hex, dec, name) => {
    if (name) return NAMED_ENTITIES[name.toLowerCase()];
    const code = hex ? parseInt(hex, 16) : Number(dec);
    return code > 0 && code <= 0x10ffff && !(code >= 0xd800 && code <= 0xdfff) ? String.fromCodePoint(code) : ' ';
  });
}

// One value as a single line of plain text: entities decoded, markup and
// invisible characters removed, whitespace collapsed, bounded.
function plain(value, max = L.maxValueChars, depth = 0) {
  if (depth > 3) return undefined;
  if (Array.isArray(value)) {
    for (const entry of value.slice(0, 4)) {
      const text = plain(entry, max, depth + 1);
      if (text) return text;
    }
    return undefined;
  }
  if (record(value)) return plain(own(value, '@value') ?? own(value, 'name'), max, depth + 1);
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : undefined;
  if (typeof value !== 'string') return undefined;
  const text = decodeEntities(value.slice(0, max * 8)).replace(/<[^<>]{0,200}>/g, ' ').replace(INVISIBLE, ' ')
    .replace(/\s+/g, ' ').trim();
  return text ? cap(text, max) : undefined;
}

function typeName(raw, inSchema) {
  if (typeof raw !== 'string') return undefined;
  const value = raw.trim();
  const name = value.replace(SCHEMA_PREFIX, '');
  if (!/^[A-Za-z]{2,40}$/.test(name)) return undefined;
  return inSchema || name !== value ? name : undefined;
}

function kindOf(node, inSchema) {
  const raw = own(node, '@type');
  for (const entry of (Array.isArray(raw) ? raw.slice(0, 8) : [raw])) {
    const type = typeName(entry, inSchema);
    if (!type) continue;
    if (EVENT_TYPES.has(type)) return {kind: 'event', type};
    if (PRODUCT_TYPES.has(type)) return {kind: 'product', type};
    if (OFFER_TYPES.has(type)) return {kind: 'offer', type};
    if (ORGANIZATION_TYPES.has(type)) return {kind: 'organization', type};
  }
  return undefined;
}

const typesOf = node => {
  const raw = own(node, '@type');
  return (Array.isArray(raw) ? raw.slice(0, 8) : [raw]).map(entry => typeName(entry, true)).filter(Boolean);
};

function mentionsSchema(context, depth = 0) {
  if (typeof context === 'string') return SCHEMA_CONTEXT.test(context.trim());
  if (depth > 2) return false;
  if (Array.isArray(context)) return context.slice(0, 8).some(entry => mentionsSchema(entry, depth + 1));
  if (record(context)) {
    return ['@vocab', 'schema'].some(key => typeof own(context, key) === 'string' &&
      SCHEMA_CONTEXT.test(own(context, key).trim()));
  }
  return false;
}

// ---- JSON-LD -------------------------------------------------------------

// Where a tag ends, as the HTML tokenizer finds it: at the first `>` outside
// a quoted attribute value. null when the document ends inside the tag.
function tagEnd(html, from) {
  let state = 'attributes';
  for (let index = from; index < html.length; index += 1) {
    const char = html[index];
    if (state === 'attributes') {
      if (char === '>') return index + 1;
      if (char === '=') state = 'value';
    } else if (state === 'value') {
      if (char === '"' || char === "'") {
        index = html.indexOf(char, index + 1);
        if (index < 0) return null;
        state = 'attributes';
      } else if (char === '>') {
        return index + 1;
      } else if (!/[\t\n\f\r ]/.test(char)) {
        state = 'unquoted';
      }
    } else if (char === '>') {
      return index + 1;
    } else if (/[\t\n\f\r ]/.test(char)) {
      state = 'attributes';
    }
  }
  return null;
}

function attributes(text) {
  const found = new Map();
  const pattern = /([^\t\n\f\r "'<>/=]+)(?:[\t\n\f\r ]*=[\t\n\f\r ]*(?:"([^"]*)"|'([^']*)'|([^\t\n\f\r "'=<>`]+)))?/g;
  for (let match; found.size < 32 && (match = pattern.exec(text));) {
    const name = match[1].toLowerCase();
    if (!found.has(name)) found.set(name, decodeEntities(match[2] ?? match[3] ?? match[4] ?? ''));
  }
  return found;
}

// Elements whose content is text, not markup, or not part of the document.
const RAW_TEXT_NAMES = ['script', 'style', 'textarea', 'title', 'noscript', 'template', 'xmp', 'iframe', 'noembed',
  'noframes'];
const RAW_TEXT = new Set(RAW_TEXT_NAMES);
const closerOf = name => new RegExp(`</${name}(?=[\\t\\n\\f\\r />]|$)`, 'gi');

// The end of a raw-text element's content and of its end tag, from `from`.
function rawTextEnd(html, name, from) {
  const closer = closerOf(name);
  closer.lastIndex = from;
  const close = closer.exec(html);
  if (!close) return {content: html.length, end: html.length};
  return {content: close.index, end: tagEnd(html, close.index + close[0].length) ?? html.length};
}

// The text of each <script type="application/ld+json"> in the document,
// skipping comments and the content of other raw-text elements.
function jsonLdSources(html) {
  const sources = [];
  const open = new RegExp(`<!--|<(${RAW_TEXT_NAMES.join('|')})(?=[\\t\\n\\f\\r />]|$)`, 'gi');
  let total = 0;
  for (let match; sources.length < L.maxScripts && (match = open.exec(html));) {
    if (match[0] === '<!--') {
      const close = html.indexOf('-->', match.index + 4);
      if (close < 0) break;
      open.lastIndex = close + 3;
      continue;
    }
    const name = match[1].toLowerCase();
    const start = tagEnd(html, match.index + match[0].length);
    if (start === null) break;
    const {content, end} = rawTextEnd(html, name, start);
    if (name === 'script') {
      const type = (attributes(html.slice(match.index + match[0].length, Math.min(start, match.index + L.maxTagChars)))
        .get('type') ?? '').split(';', 1)[0].trim().toLowerCase();
      const length = content - start;
      if (type === 'application/ld+json' && length <= L.maxScriptChars && total + length <= L.maxJsonChars) {
        total += length;
        sources.push(html.slice(start, content));
      }
    }
    open.lastIndex = end;
  }
  return sources;
}

function parseJsonLd(source) {
  const text = source.trim()
    .replace(/^(?:<!--|\/\/\s*<!\[CDATA\[|\/\*\s*<!\[CDATA\[\s*\*\/|<!\[CDATA\[)/, '')
    .replace(/(?:-->|\/\/\s*\]\]>|\/\*\s*\]\]>\s*\*\/|\]\]>)$/, '')
    // A raw control character is invalid in JSON strings and plain
    // whitespace elsewhere; publishers often leave raw newlines in strings.
    .replace(/[\u0000-\u001f]/g, ' ');
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

// ---- Microdata -----------------------------------------------------------

const VOID = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source',
  'track', 'wbr']);
// Elements a browser closes when the next one of the same name opens.
const SELF_CLOSING_SIBLINGS = new Set(['li', 'p', 'dt', 'dd', 'tr', 'td', 'th', 'option']);
// Elements whose boundary separates words in rendered text.
const BLOCK = new Set(['br', 'p', 'div', 'li', 'tr', 'td', 'th', 'dd', 'dt', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
  'section', 'article', 'address', 'ul', 'ol', 'table', 'header', 'footer']);
const URL_VALUED = {a: 'href', area: 'href', link: 'href', audio: 'src', embed: 'src', iframe: 'src', img: 'src',
  source: 'src', track: 'src', video: 'src', object: 'data'};
// Properties whose value is a URL or an enumeration member. Pages often put
// itemprop="name" on a link; a link's text is its value for anything else.
const URL_PROPERTIES = new Set(['url', 'sameAs', 'image', 'logo', 'availability', 'itemCondition', 'eventStatus',
  'mainEntityOfPage']);

function propNames(value) {
  if (typeof value !== 'string') return [];
  return value.trim().split(/[\t\n\f\r ]+/).slice(0, 4).map(name => name.replace(SCHEMA_PREFIX, ''))
    .filter(name => /^[A-Za-z][A-Za-z0-9]{0,40}$/.test(name));
}

function microdataType(value) {
  if (typeof value !== 'string') return undefined;
  for (const entry of value.trim().split(/[\t\n\f\r ]+/).slice(0, 4)) {
    if (/^https?:\/\/(?:www\.)?schema\.org\/[A-Za-z]{2,40}$/i.test(entry)) return entry.replace(SCHEMA_PREFIX, '');
  }
  return undefined;
}

// Microdata items as plain objects shaped like JSON-LD nodes. A simplified
// HTML tree walk: void elements never open, an end tag closes the nearest
// open element of its name, and li/p/td/... close an open sibling.
function microdataRoots(html, baseUrl) {
  if (!/\bitemscope\b/i.test(html) || !/\bitemtype[\t\n\f\r ]*=[\t\n\f\r ]*["']?[\t\n\f\r ]*https?:\/\/(?:www\.)?schema\.org\//i.test(html)) {
    return [];
  }
  const roots = [];
  const stack = [];
  const capturing = [];
  const newItem = type => ({type, props: new Map(), count: 0});
  const nearestItem = () => {
    for (let i = stack.length - 1; i >= 0; i -= 1) if (stack[i].item) return stack[i].item;
    return undefined;
  };
  const addProp = (item, names, value) => {
    for (const name of names) {
      if (item.count >= 64) return;
      const values = item.props.get(name) ?? [];
      if (values.length >= 8) continue;
      values.push(value);
      item.props.set(name, values);
      item.count += 1;
    }
  };
  const appendText = text => {
    for (const entry of capturing) {
      if (entry.capture.length < L.maxCaptureChars) entry.capture += text.slice(0, L.maxCaptureChars - entry.capture.length);
    }
  };
  const finish = entry => {
    if (entry.capture === undefined) return;
    const at = capturing.lastIndexOf(entry);
    if (at >= 0) capturing.splice(at, 1);
    if (entry.owner.count >= 64) return;
    const value = plain(entry.capture, L.maxCaptureChars);
    if (value) addProp(entry.owner, entry.names, value);
  };
  const close = name => {
    const floor = Math.max(0, stack.length - 256);
    let at = stack.length - 1;
    while (at >= floor && stack[at].name !== name) at -= 1;
    if (at < floor) return;
    while (stack.length > at) finish(stack.pop());
  };
  const valueOf = (name, attrs, names) => {
    // `content` is meta's value; schema.org's own examples (and search
    // engines) also accept it on other elements: <span itemprop="price"
    // content="549.99">$549.99</span>.
    if (name === 'meta' || attrs.has('content')) return attrs.get('content');
    if (URL_VALUED[name] && (name !== 'a' && name !== 'area' || names.some(prop => URL_PROPERTIES.has(prop)))) {
      const raw = attrs.get(URL_VALUED[name]);
      return raw === undefined ? undefined : link(raw, baseUrl) ?? '';
    }
    if (name === 'data' || name === 'meter') return attrs.get('value');
    if (name === 'time' && attrs.has('datetime')) return attrs.get('datetime');
    return undefined;
  };

  let index = 0, itemTags = 0;
  for (let tags = 0; index < html.length && tags < L.maxTags; tags += 1) {
    const lt = html.indexOf('<', index);
    const stop = lt < 0 ? html.length : lt;
    if (capturing.length && stop > index) appendText(html.slice(index, Math.min(stop, index + L.maxCaptureChars)));
    if (lt < 0) break;
    if (html.startsWith('<!--', lt)) {
      const end = html.indexOf('-->', lt + 4);
      index = end < 0 ? html.length : end + 3;
      continue;
    }
    const head = /^<(\/?)([A-Za-z][A-Za-z0-9:-]{0,40})/.exec(html.slice(lt, lt + 48));
    if (!head) {
      if (capturing.length) appendText('<');
      index = lt + 1;
      continue;
    }
    const end = tagEnd(html, lt + head[0].length);
    if (end === null) break;
    const name = head[2].toLowerCase();
    index = end;
    if (capturing.length && BLOCK.has(name)) appendText(' ');
    if (head[1]) {
      close(name);
      continue;
    }
    if (RAW_TEXT.has(name)) {
      index = rawTextEnd(html, name, end).end;
      continue;
    }
    if (SELF_CLOSING_SIBLINGS.has(name) && stack.at(-1)?.name === name) finish(stack.pop());
    const attrText = html.slice(lt + head[0].length, Math.min(end, lt + L.maxTagChars));
    const marked = /item(?:scope|prop)/i.test(attrText);
    if (marked && ++itemTags > L.maxItemTags) break;
    const attrs = marked ? attributes(attrText) : new Map();
    const names = propNames(attrs.get('itemprop'));
    const owner = names.length ? nearestItem() : undefined;
    const entry = {name};
    if (attrs.has('itemscope')) {
      entry.item = newItem(microdataType(attrs.get('itemtype')));
      if (owner) addProp(owner, names, entry.item);
      else if (roots.length < 64) roots.push(entry.item);
    } else if (owner) {
      const value = valueOf(name, attrs, names);
      if (value !== undefined) addProp(owner, names, value);
      else if (!VOID.has(name) && capturing.length < 32 && owner.count < 64) Object.assign(entry, {owner, names, capture: ''});
    }
    if (VOID.has(name)) continue;
    if (stack.length >= L.maxStack) break;
    stack.push(entry);
    if (entry.capture !== undefined) capturing.push(entry);
  }
  while (stack.length) finish(stack.pop());

  const toNode = (item, depth) => {
    const node = {'@type': item.type};
    for (const [name, values] of item.props) {
      const converted = values.map(value => (typeof value === 'string' ? value
        : depth < 6 ? toNode(value, depth + 1) : undefined)).filter(value => value !== undefined);
      if (converted.length) node[name] = converted.length === 1 ? converted[0] : converted;
    }
    return node;
  };
  return roots.map(item => toNode(item, 0));
}

// ---- Items and rendering -------------------------------------------------

function link(value, baseUrl) {
  const raw = typeof value === 'string' ? value : Array.isArray(value) ? value[0]
    : record(value) ? own(value, 'url') ?? own(value, '@id') : undefined;
  if (typeof raw !== 'string' || !raw.trim() || raw.length > L.maxUrlChars * 2) return undefined;
  try {
    const url = new URL(raw.trim(), baseUrl);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return undefined;
    // Campaign tags do not change the document: "?utm_source=microformat".
    for (const key of [...url.searchParams.keys()]) if (/^utm_/i.test(key)) url.searchParams.delete(key);
    const href = url.href;
    return href.length <= L.maxUrlChars && !/[\s<>"'`]/.test(href) ? href : undefined;
  } catch {
    return undefined;
  }
}

// The document a URL names, for comparing a node's url with the page's.
function documentKey(value) {
  try {
    const url = new URL(value);
    for (const key of [...url.searchParams.keys()]) if (/^utm_/i.test(key)) url.searchParams.delete(key);
    // One serialization of the query, whether the page wrote "9/26" or "9%2F26".
    return `${url.hostname.toLowerCase().replace(/^www\./, '')}${url.pathname.replace(/\/+$/, '')}?${url.searchParams}`;
  } catch {
    return undefined;
  }
}

const ISO_DATE_TIME = /^(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2})(?::\d{2}(?:[.,]\d{1,9})?)?)?[\t ]*(Z|[+-]\d{2}(?::?\d{2})?)?$/i;

// An ISO 8601 date as "2026-10-02 20:00 UTC-0400"; other text is kept as is.
// The zone has no colon, so it cannot read as a time of day.
function when(value) {
  const text = plain(value, 48);
  if (!text) return undefined;
  const match = ISO_DATE_TIME.exec(text);
  if (!match) return cap(text, 40);
  if (!match[2]) return match[1];
  const zone = !match[3] ? '' : /^z$/i.test(match[3]) ? ' UTC' : ` UTC${match[3].replace(':', '').padEnd(5, '0')}`;
  return `${match[1]} ${match[2]}${zone}`;
}

function enumWords(value, words) {
  const name = plain(Array.isArray(value) ? value[0] : value, 80)?.replace(SCHEMA_PREFIX, '');
  return name && Object.hasOwn(words, name) ? words[name] : undefined;
}

function priceText(value) {
  const text = plain(value, 24);
  return text && /^\d[\d,. ]{0,18}$/.test(text) ? text.trim() : undefined;
}

function address(value, ids) {
  const node = resolve(Array.isArray(value) ? value[0] : value, ids);
  if (typeof node === 'string') return plain(node, 200);
  if (!record(node)) return undefined;
  const parts = ['streetAddress', 'addressLocality', 'addressRegion', 'postalCode', 'addressCountry']
    .map(key => plain(own(node, key), 80)).filter(Boolean);
  return parts.length ? cap([...new Set(parts)].join(', '), 200) : undefined;
}

function places(value, ids) {
  const found = (Array.isArray(value) ? value : [value]).slice(0, 2).map(entry => {
    const node = resolve(entry, ids);
    if (typeof node === 'string') return plain(node, 200);
    if (!record(node)) return undefined;
    if (typesOf(node).includes('VirtualLocation')) return 'online';
    const name = plain(own(node, 'name'), 120);
    const where = address(own(node, 'address'), ids);
    return [name, where && where !== name ? where : undefined].filter(Boolean).join(', ') || undefined;
  }).filter(Boolean);
  return found.length ? cap([...new Set(found)].join(' / '), 260) : undefined;
}

function parties(value, ids) {
  const names = (Array.isArray(value) ? value : [value]).slice(0, 4)
    .map(entry => {
      const node = resolve(entry, ids);
      return typeof node === 'string' ? plain(node, 100) : record(node) ? plain(own(node, 'name'), 100) : undefined;
    }).filter(Boolean);
  return names.length ? [...new Set(names)].slice(0, 2).join(', ') : undefined;
}

// A node that is only a reference ({"@id": ...}) is the node it names.
function resolve(value, ids) {
  if (!record(value)) return value;
  const id = own(value, '@id');
  if (typeof id === 'string' && Object.keys(value).length === 1) return ids.get(id) ?? value;
  return value;
}

function offerText(value, ids) {
  const node = resolve(value, ids);
  if (!record(node)) return undefined;
  let specification = resolve(own(node, 'priceSpecification'), ids);
  if (Array.isArray(specification)) specification = specification.find(entry => record(entry) && own(entry, 'price') !== undefined);
  const currencyText = plain(own(node, 'priceCurrency') ?? own(specification, 'priceCurrency'), 8)?.toUpperCase();
  const currency = currencyText && /^[A-Z]{3}$/.test(currencyText) ? currencyText : undefined;
  const low = priceText(own(node, 'lowPrice'));
  const high = priceText(own(node, 'highPrice'));
  const single = priceText(own(node, 'price')) ?? priceText(own(specification, 'price'));
  const amount = single ?? (low && high && low !== high ? `${low}-${high}` : low ?? high);
  const count = Number(plain(own(node, 'offerCount'), 12));
  const availability = enumWords(own(node, 'availability'), AVAILABILITY);
  if (!amount && !availability) return undefined;
  const seller = parties(own(node, 'seller'), ids);
  const validUntil = when(own(node, 'priceValidUntil'));
  return [amount && `${amount}${currency ? ` ${currency}` : ''}`,
    Number.isSafeInteger(count) && count > 1 ? `${count} offers` : undefined,
    availability, enumWords(own(node, 'itemCondition'), CONDITION),
    seller && `seller ${seller}`, validUntil && `price valid until ${validUntil}`].filter(Boolean).join(', ');
}

function line(type, name, fields) {
  const parts = fields.filter(([, value]) => value).map(([label, value]) => `${label}: ${value}`);
  return cap(`- ${type}${name ? `: ${name}` : ''}${parts.length ? ` | ${parts.join(' | ')}` : ''}`, L.maxLineChars);
}

function render({kind, type, node}, ids, baseUrl) {
  const name = plain(own(node, 'name'));
  const url = link(own(node, 'url'), baseUrl);
  if (kind === 'event') {
    const start = when(own(node, 'startDate'));
    if (!start) return undefined;
    const end = when(own(node, 'endDate'));
    // An end on the start's day without a time says nothing more.
    const sameDay = end === start || end === start.slice(0, 10);
    return line(type, name, [['start', start], ['end', sameDay ? undefined : end],
      ['status', enumWords(own(node, 'eventStatus'), EVENT_STATUS)], ['location', places(own(node, 'location'), ids)],
      ['organizer', parties(own(node, 'organizer'), ids)], ['url', url]]);
  }
  if (kind === 'product') {
    const offers = own(node, 'offers');
    const offerLines = (Array.isArray(offers) ? offers : [offers]).slice(0, L.maxOffers)
      .map(offer => offerText(offer, ids)).filter(Boolean);
    const sku = plain(own(node, 'sku'), 64);
    const gtin = ['gtin', 'gtin14', 'gtin13', 'gtin12', 'gtin8'].map(key => plain(own(node, key), 20))
      .find(value => value && /^\d{8,14}$/.test(value));
    if (!offerLines.length && !sku && !gtin) return undefined;
    return line(type, name, [['sku', sku], ['gtin', gtin], ...offerLines.map(offer => ['offer', offer]), ['url', url]]);
  }
  if (kind === 'offer') {
    const offer = offerText(node, ids);
    if (!offer) return undefined;
    const item = resolve(own(node, 'itemOffered'), ids);
    return line(type, plain(typeof item === 'string' ? item : own(item, 'name')) ?? name, [['offer', offer], ['url', url]]);
  }
  const where = address(own(node, 'address'), ids);
  if (!name || !where) return undefined;
  return line(type, name, [['address', where], ['url', url]]);
}

// The whitelisted schema.org items of a page, rendered one per line.
// {lines, omitted}: `omitted` counts renderable items left out by the caps.
export function structuredDataItems(html, {baseUrl} = {}) {
  if (typeof html !== 'string' || !html) return {lines: [], omitted: 0};
  const roots = [];
  for (const source of jsonLdSources(html)) {
    const parsed = parseJsonLd(source);
    if (parsed !== undefined) roots.push({node: parsed, schema: false});
  }
  for (const node of microdataRoots(html, baseUrl)) roots.push({node, schema: true});

  const ids = new Map();
  const found = [];
  const seen = new Set();
  let budget = L.maxNodes;
  const credited = new Set();
  const walk = (node, depth, inSchema, credit) => {
    if (budget <= 0 || depth > L.maxDepth || !node || typeof node !== 'object') return;
    budget -= 1;
    if (Array.isArray(node)) {
      for (const child of node.slice(0, 500)) walk(child, depth + 1, inSchema, credit);
      return;
    }
    if (seen.has(node)) return;
    seen.add(node);
    const schema = inSchema || mentionsSchema(own(node, '@context'));
    const id = own(node, '@id');
    if (typeof id === 'string' && Object.keys(node).length > 1 && ids.size < 2_000 && !ids.has(id)) ids.set(id, node);
    const kind = kindOf(node, schema);
    if (kind) found.push({...kind, node, credit});
    // An event's, product's or offer's own fields are rendered with it.
    if (kind && kind.kind !== 'organization') return;
    for (const key of Object.keys(node).slice(0, 200)) {
      const value = node[key];
      if (key === '@context' || !value || typeof value !== 'object') continue;
      const creditKey = CREDIT_KEYS.has(key);
      if (creditKey) {
        for (const entry of (Array.isArray(value) ? value : [value]).slice(0, 20)) {
          if (typeof own(entry, '@id') === 'string') credited.add(own(entry, '@id'));
        }
      }
      walk(value, depth + 1, schema, credit || creditKey);
    }
  };
  for (const root of roots) walk(root.node, 0, root.schema, false);
  // A publisher, author or brand is the site or a maker, not a subject.
  const subject = entry => entry.kind !== 'organization' ||
    (!entry.credit && !credited.has(own(entry.node, '@id')));

  // On an item's own page, its `url` names the page: other items (related
  // events, other products) are left out. `@id` does not count: every node
  // on the page may have one there ("#offer").
  const pageKey = baseUrl ? documentKey(baseUrl) : undefined;
  const ownItem = entry => {
    if (entry.kind === 'organization' || pageKey === undefined) return false;
    const href = link(own(entry.node, 'url'), baseUrl);
    return href !== undefined && documentKey(href) === pageKey;
  };
  const subjects = found.filter(subject);
  const main = subjects.filter(entry => ownItem(entry) && render(entry, ids, baseUrl));
  const candidates = main.length ? [...main, ...subjects.filter(entry => entry.kind === 'organization')] : subjects;

  // A product's offers are shown on the product's line, not again.
  const productOffers = new Set();
  for (const entry of candidates) {
    if (entry.kind !== 'product') continue;
    const offers = own(entry.node, 'offers');
    for (const offer of (Array.isArray(offers) ? offers : [offers]).slice(0, 50)) productOffers.add(resolve(offer, ids));
  }
  const lines = [];
  let items = 0, organizations = 0, omitted = 0, chars = 0;
  const ordered = [...candidates.filter(entry => entry.kind !== 'organization' && !productOffers.has(entry.node)),
    ...candidates.filter(entry => entry.kind === 'organization')].slice(0, 200);
  for (const entry of ordered) {
    const text = render(entry, ids, baseUrl);
    if (!text || lines.includes(text)) continue;
    const organization = entry.kind === 'organization';
    if ((organization ? organizations >= L.maxOrganizations : items >= L.maxItems) ||
        chars + text.length + 1 > L.maxBlockChars) {
      omitted += 1;
      continue;
    }
    if (organization) organizations += 1;
    else items += 1;
    chars += text.length + 1;
    lines.push(text);
  }
  return {lines, omitted};
}

// The block the reader puts before a page's visible text, or "".
export function structuredDataBlock(html, {baseUrl} = {}) {
  let result;
  try {
    result = structuredDataItems(html, {baseUrl});
  } catch {
    return '';
  }
  if (!result.lines.length) return '';
  return [STRUCTURED_DATA_HEADER, ...result.lines,
    ...(result.omitted ? [`- (${result.omitted} more schema.org item${result.omitted === 1 ? '' : 's'} not shown)`] : [])]
    .join('\n');
}

export function withStructuredData(text, block) {
  return block ? `${block}\n\n${text ?? ''}` : text;
}
