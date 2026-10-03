import { isDeepStrictEqual } from "node:util";

const record = (value) => Boolean(value) && typeof value === "object" && !Array.isArray(value);

export const TRUNCATED_FETCH_EXTRACTION_GUIDANCE = "ODS reading guidance (not source evidence): This successful page read was truncated; only its returned text is evidence. If a needed fact is missing, discover pixel_ods_web_extract and use its exact schema: {\"url\":\"actual source URL from this receipt\",\"query\":\"short literal identifier for the missing detail\"}. It can locate evidence beyond a page prefix. Use it only when needed and within the remaining page-reading and total allowances; do not repeat denied calls. If the returned excerpt already suffices, continue with it. Do not infer absence from a truncated prefix or claim unread facts were verified. If evidence remains unavailable, report that limitation.";

// Called only on a result already bound to its exact call/run/params. Retain
// a boolean, not another copy of webpage text or a larger evidence window.
export function successfulTruncatedFetch(result) {
  const details = result?.details;
  return result?.isError !== true && record(details) && details.truncated === true &&
    Number.isInteger(details.status) && details.status >= 200 && details.status < 300 &&
    typeof details.text === 'string' && details.text.trim().length > 0 &&
    ['text/html', 'application/xhtml+xml', 'text/plain', 'text/markdown', 'application/json']
      .includes(String(details.contentType ?? '').split(';', 1)[0].trim().toLowerCase());
}

export function projectNativeFetchGuidance(message, successfulTruncated) {
  if (successfulTruncated !== true || !record(message) || message.role !== 'toolResult' ||
      message.toolName !== 'web_fetch' || message.isError === true || !Array.isArray(message.content) ||
      message.content.length === 0 || !message.content.every(block => block?.type === 'text' && typeof block.text === 'string')) return undefined;
  // Preserve the persisted text/details exactly, including any framework cap.
  return {...message, content:[...message.content, {type:'text', text:TRUNCATED_FETCH_EXTRACTION_GUIDANCE}]};
}

const MAX_SEARCH_TEXT_CHARS = 256 * 1024;
export const SEARCH_SOURCE_EVIDENCE_GUIDANCE = "ODS research guidance (not source evidence): Search hits are leads. Read the selected source and match the exact requested entity, variant, place and date before citing a claim. A related model or event is not interchangeable. Check publisher identity before calling a page official; resellers and aggregators are independent sources. Report unavailable evidence honestly. A price or Add to Cart button alone does not establish in-stock availability.";
export const EMPTY_SEARCH_RECOVERY_GUIDANCE = "ODS search recovery (not source evidence): This query returned no results; that does not prove the requested information is absent. Within the remaining research allowance, try one shorter query for one entity and one fact. Remove optional date, availability or multiple-retailer qualifiers while retaining required identity constraints. Check dates and availability on the returned pages. If a useful source was already found, read it rather than repeating discovery. Do not repeat the same empty query or invent a citation.";
// Match the pinned native after-tool sanitizer for source text, even when
// structured details remain complete. Fixed ODS guidance is a separate block.
const NATIVE_SEARCH_TEXT_CHARS = 8_000;
function nativeSearchText(text) {
  if (text.length <= NATIVE_SEARCH_TEXT_CHARS) return text;
  let end = NATIVE_SEARCH_TEXT_CHARS;
  const high = text.charCodeAt(end - 1), low = text.charCodeAt(end);
  if (high >= 0xD800 && high <= 0xDBFF && low >= 0xDC00 && low <= 0xDFFF) end -= 1;
  return `${text.slice(0, end)}\n…(truncated)…`;
}

export const OMITTED_SEARCH_SNIPPETS_GUIDANCE = "ODS search projection (not source evidence): Some descriptions or excerpts were omitted to fit the native result limit while preserving complete source titles, URLs and metadata. Search hits remain leads; read the selected pages within the existing research allowance before making claims. Omitted snippets do not establish that a fact is absent.";

function boundedNativeSearchContent(payload, text) {
  if (text.length <= NATIVE_SEARCH_TEXT_CHARS) return { text, omitted: false };
  // Compact JSON first. Never cut a URL, title, trust wrapper or JSON string.
  const compact = JSON.stringify(payload);
  if (compact.length <= NATIVE_SEARCH_TEXT_CHARS) return { text: compact, omitted: false };
  const results = payload.results.map(row => ({ ...row }));
  const snippets = results.flatMap((row, index) => ['description', 'excerpts']
    .filter(key => Object.hasOwn(row, key))
    .map(key => ({ index, key, size: JSON.stringify(row[key]).length })));
  // Omit whole fields, longest first, rather than letting one large page
  // description hide later leads. Preserve every other field byte-for-byte.
  snippets.sort((a, b) => b.size - a.size);
  for (const { index, key } of snippets) {
    delete results[index][key];
    const projected = JSON.stringify({ ...payload, results });
    if (projected.length <= NATIVE_SEARCH_TEXT_CHARS) return { text: projected, omitted: true };
  }
  // Unusually large metadata/identities cannot fit without changing evidence.
  // Retain the existing bounded native fallback, not a made-up shorter URL.
  return { text: nativeSearchText(text), omitted: false };
}

// The native web tool serializes its structured payload as one JSON block.
// First remove excerpts already present byte-for-byte as that same result's
// description. Native projection may then omit whole snippets to retain leads
// within the existing text cap; the original structured receipt stays intact.
function deduplicatedSearchContent(result, native = false) {
  if (!record(result) || !record(result.details) ||
      !Array.isArray(result.content) || result.content.length !== 1 ||
      result.content[0]?.type !== "text" || typeof result.content[0].text !== "string" ||
      result.content[0].text.length > MAX_SEARCH_TEXT_CHARS) return undefined;
  let payload;
  try { payload = JSON.parse(result.content[0].text); }
  catch {
    // Only the exact pinned sanitizer output can stand in for complete JSON.
    // Arbitrary partial text or differently redacted details are not evidence.
    if (!native) return undefined;
    let serialized;
    try {
      serialized = JSON.stringify(result.details, null, 2);
      if (typeof serialized !== "string" || !isDeepStrictEqual(JSON.parse(serialized), result.details)) return undefined;
    } catch { return undefined; }
    if (serialized.length <= NATIVE_SEARCH_TEXT_CHARS || serialized.length > MAX_SEARCH_TEXT_CHARS ||
        result.content[0].text !== nativeSearchText(serialized)) return undefined;
    payload = result.details;
  }
  if (!record(payload) || !isDeepStrictEqual(payload, result.details) ||
      typeof payload.provider !== "string" || !Array.isArray(payload.results) ||
      payload.results.length > 40) return undefined;
  let changed = false;
  const results = [];
  for (const row of payload.results) {
    if (!record(row)) return undefined;
    if (!Object.hasOwn(row, "excerpts")) { results.push(row); continue; }
    if (typeof row.description !== "string" || !Array.isArray(row.excerpts) ||
        !row.excerpts.every((text) => typeof text === "string")) return undefined;
    const excerpts = row.excerpts.filter((text) => text !== row.description);
    if (excerpts.length === row.excerpts.length) { results.push(row); continue; }
    changed = true;
    const projected = { ...row, excerpts };
    if (excerpts.length === 0) delete projected.excerpts;
    results.push(projected);
  }
  if (!changed && result.isError === true) return undefined;
  const projectedPayload = changed ? { ...payload, results } : payload;
  const text = changed ? JSON.stringify(projectedPayload, null, 2) : result.content[0].text;
  const bounded = native && result.isError !== true
    ? boundedNativeSearchContent(projectedPayload, text)
    : { text: native ? nativeSearchText(text) : text, omitted: false };
  // Fixed ODS guidance is separate from the unchanged evidence/receipt. It
  // grants no calls or authority and does not expand the source-text cap.
  const guidance = result.isError === true ? [] : [{ type: "text", text:
    payload.results.length === 0 ? EMPTY_SEARCH_RECOVERY_GUIDANCE : SEARCH_SOURCE_EVIDENCE_GUIDANCE }];
  return [{ ...result.content[0], text: bounded.text }, ...guidance,
    ...(bounded.omitted ? [{type: 'text', text: OMITTED_SEARCH_SNIPPETS_GUIDANCE}] : [])];
}

// Called only after exact native call/run/params binding by the guard. Snapshot
// before framework persistence truncation. The native hook may already have
// capped text; retain only an exactly verified serialization of its details.
export function captureNativeWebSearchResult(result) {
  if (!deduplicatedSearchContent(result, true)) return undefined;
  try { return structuredClone(result); }
  catch { return undefined; }
}

export function projectNativeWebSearchResult(message, result) {
  if (!record(message) || message.role !== "toolResult" || message.toolName !== "web_search") return undefined;
  const content = deduplicatedSearchContent(result, true);
  if (!content) return undefined;
  return { ...message, ...(message.isError === true || result.isError === true ? { isError: true } : {}),
    content, details: result.details };
}

// Preserve native evidence blocks instead of serializing them inside a second
// JSON document. The caller binds this framework envelope to the exact call.
export function projectWebResult(message, envelope, allowFetchGuidance = true) {
  if (!record(message) || message.toolName !== "tool_call" || !record(envelope)) return undefined;
  const { tool, result } = envelope;
  if (!record(tool) || !record(result) || tool.source !== "openclaw" ||
      tool.sourceName !== "core" || !["web_search", "web_fetch"].includes(tool.name) ||
      tool.id !== `openclaw:core:${tool.name}`) return undefined;
  const content = result.content;
  if (!Array.isArray(content) || content.length === 0 ||
      !content.every((block) => record(block) && block.type === "text" && typeof block.text === "string")) {
    return undefined;
  }
  const metadata = { ...result };
  delete metadata.content;
  if (content.length === 1 && record(result.details)) {
    let duplicate = false;
    try { duplicate = isDeepStrictEqual(JSON.parse(content[0].text), result.details); }
    catch { /* Plain text is not a duplicate structured payload. */ }
    if (duplicate) delete metadata.details;
    else if (result.details.aggregated === content[0].text) {
      metadata.details = { ...result.details };
      delete metadata.details.aggregated;
    }
  }
  const failed = message.isError === true || result.isError === true;
  const identity = { id: tool.id, source: tool.source, sourceName: tool.sourceName, name: tool.name };
  return {
    ...message,
    ...(failed ? { isError: true } : {}),
    content: [
      { type: "text", text: JSON.stringify({ tool: identity, result: metadata, ...(failed ? { isError: true } : {}) }) },
      ...(tool.name === "web_search" ? deduplicatedSearchContent(result) ?? content : content).map((block) => ({ ...block })),
      ...(allowFetchGuidance && !failed && tool.name === 'web_fetch' && successfulTruncatedFetch(result)
        ? [{type:'text', text:TRUNCATED_FETCH_EXTRACTION_GUIDANCE}] : []),
    ],
    details: envelope,
  };
}
