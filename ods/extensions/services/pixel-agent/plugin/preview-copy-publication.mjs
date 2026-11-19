import {ownerLaneText} from './owner-tool-policy.mjs';

// A named input-only original and its explicitly requested copied preview.
// This parses scope, never grants publication itself. The guard evaluates all
// remaining owner restrictions and enforces the exact destination on each call.
const COMPONENT = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const OPERAND = String.raw`(?:/workspace/)?[A-Za-z0-9][A-Za-z0-9._/-]{0,511}/index\.html`;
const READ = new RegExp(String.raw`^read\s+(${OPERAND})\s+as\s+input\s+only$`, 'i');
const COPY = new RegExp(String.raw`^(?:please\s+)?copy\s+(it|${OPERAND})\s+(?:to|into)\s+(?:the\s+)?new\s+(${OPERAND})(?:\s+before\s+editing)?$`, 'i');
const ORIGINAL_ONLY = /^(?:please\s+)?(?:do\s+not|don['’]t|never|must\s+not|should\s+not)\s+(?:(?:edit|change|modify|overwrite)\s+(?:or|and)\s+)?(?:publish|republish|preview|display|show|serve)\s+the\s+original(?:\s+(?:site|page|project|file))?$/i;

function entry(path) {
  const value = path.replace(/^\/workspace\//, '');
  const parts = value.split('/');
  if (value.length > 512 || parts.length < 2 || parts.length > 13 ||
      parts.at(-1) !== 'index.html' || !parts.every(part => COMPONENT.test(part))) return undefined;
  return value;
}


function clauses(text) {
  const result = [];
  let start = 0;
  for (const boundary of [...text.matchAll(/[!?;\n]+|\.(?=\s|$)/g), {index:text.length, 0:''}]) {
    const value = text.slice(start, boundary.index).trim();
    if (value) result.push({start, end:boundary.index, text:value});
    start = boundary.index + boundary[0].length;
  }
  return result;
}

function escape(value) { return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

export function extractCopyPublication(text) {
  if (typeof text !== 'string' || !text.trim() || text.length > 65536) return undefined;
  const owner = ownerLaneText(text);
  // An explicitly embedded payload is data, not the owner's copy command.
  // Ambiguous unquoted multi-sentence payloads do not open publication.
  if (/\b(?:containing|with\s+(?:the\s+)?(?:contents?|text)|(?:following|example|sample)\s+(?:text|instructions?|prompt))\b/i.test(owner)) return undefined;
  const all = clauses(owner);
  const exclusions = all.filter(row => ORIGINAL_ONLY.test(row.text));
  if (exclusions.length !== 1) return undefined;
  const copyCommands = all.filter(row => /^(?:please\s+)?copy\b/i.test(row.text));
  if (copyCommands.length !== 1) return undefined;
  const copied = COPY.exec(copyCommands[0].text);
  if (!copied) return undefined;
  const inputReads = all.map(row => READ.exec(row.text)).filter(Boolean);
  let source;
  if (copied[1].toLowerCase() === 'it') {
    if (inputReads.length !== 1 || all.find(row => READ.test(row.text)).end > copyCommands[0].start) return undefined;
    source = entry(inputReads[0][1]);
  } else source = entry(copied[1]);
  const destination = entry(copied[2]);
  if (!source || !destination || source === destination ||
      inputReads.some(match => entry(match[1]) !== source)) return undefined;
  const sourceDirectory = source.slice(0, -'/index.html'.length);
  const relativeDirectory = destination.slice(0, -'/index.html'.length);
  // Publishing a parent could include the protected original in its snapshot.
  if (sourceDirectory.startsWith(relativeDirectory + '/') || relativeDirectory.startsWith(sourceDirectory + '/')) return undefined;
  const html = [...owner.matchAll(/(?:^|[^A-Za-z0-9._/-])((?:\/workspace\/)?[A-Za-z0-9][A-Za-z0-9._/-]*\.html?)(?![A-Za-z0-9_/-]|\.(?!\s|$))/gi)].map(match => entry(match[1]));
  if (html.some(path => path !== source && path !== destination)) return undefined;
  const requestPattern = new RegExp(String.raw`^(?:please\s+)?publish\s+(` + escape(relativeDirectory) +
    String.raw`(?:/index\.html)?)(?:\s+(?:with|using)\s+the\s+existing\s+workspace\s+preview\s+tool)?$`, 'i');
  const commands = all.filter(row => /^(?:please\s+)?(?:publish|republish|preview|serve)\b/i.test(row.text));
  if (commands.length !== 1) return undefined;
  const requested = requestPattern.exec(commands[0].text);
  if (!requested || requested[1].replace(/\/index\.html$/, '') !== relativeDirectory) return undefined;
  const exclusion = exclusions[0], request = commands[0];
  return Object.freeze({sourceDirectory, relativeDirectory,
    request:request.text, afterRequest:owner.slice(request.end),
    remaining:owner.slice(0, exclusion.start) + ' ' + owner.slice(exclusion.end)});
}
