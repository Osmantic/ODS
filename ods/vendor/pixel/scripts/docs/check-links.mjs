import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { readText, repoRoot, toPosix, walkFiles } from './lib.mjs';

const blank = (value) => value.replace(/[^\n]/gu, ' ');
const unescapeMarkdown = (value) => value.replace(/\\([!"#$%&'()*+,\-./:;<=>?@[\]\\^_`{|}~])/gu, '$1');
const referenceLabel = (value) => unescapeMarkdown(value).trim().replace(/\s+/gu, ' ').toLowerCase();

// Preserve offsets/newlines so failures still point at the original source line.
function withoutCodeBlocks(text) {
  let fence;
  const lines = text.replace(/\r\n?/gu, '\n').split('\n');
  return lines.map((line, index) => {
    if (index === 0 && line === '---' && lines.slice(1).includes('---')) fence = { frontMatter: true };
    else if (fence?.frontMatter && line === '---') { fence = undefined; return blank(line); }
    if (fence?.frontMatter) return blank(line);
    const match = /^ {0,3}(`{3,}|~{3,})(.*)$/u.exec(line);
    if (fence) {
      if (match && match[1][0] === fence.character && match[1].length >= fence.length && !match[2].trim()) fence = undefined;
      return blank(line);
    }
    if (match && !(match[1][0] === '`' && match[2].includes('`'))) {
      fence = { character: match[1][0], length: match[1].length };
      return blank(line);
    }
    return line;
  }).join('\n').replace(/<!--[\s\S]*?-->/gu, blank);
}

function inlineCodeEnd(text, start) {
  let openingEnd = start;
  while (text[openingEnd] === '`') openingEnd += 1;
  // Soft line breaks are allowed; block starts and blank lines end the context.
  const boundary = /\n(?:[ \t]*\n| {0,3}(?:#{1,6}(?:\s|$)|>|[-*+]\s|\d+[.)]\s))/u.exec(text.slice(openingEnd));
  const limit = boundary ? openingEnd + boundary.index : text.length;
  let cursor = openingEnd;
  while (cursor < limit) {
    cursor = text.indexOf('`', cursor);
    if (cursor < 0 || cursor >= limit) break;
    let end = cursor;
    while (text[end] === '`') end += 1;
    if (end - cursor === openingEnd - start) return end;
    cursor = end;
  }
  return openingEnd;
}

function destinationAt(text, start) {
  let cursor = start;
  while (/\s/u.test(text[cursor] ?? '') && cursor < text.length) cursor += 1;
  if (text[cursor] === '<') {
    const begin = ++cursor;
    while (cursor < text.length && text[cursor] !== '\n') {
      if (text[cursor] === '\\') cursor += 2;
      else if (text[cursor] === '>') return { destination: text.slice(begin, cursor), end: cursor + 1 };
      else cursor += 1;
    }
    return null;
  }
  const begin = cursor;
  let depth = 0;
  while (cursor < text.length && !/\s/u.test(text[cursor])) {
    if (text[cursor] === '\\') { cursor += 2; continue; }
    if (text[cursor] === '(') depth += 1;
    if (text[cursor] === ')') {
      if (depth === 0) break;
      depth -= 1;
    }
    cursor += 1;
  }
  return depth === 0 ? { destination: text.slice(begin, cursor), end: cursor } : null;
}

function closingBracket(text, start) {
  let depth = 0;
  for (let i = start; i < text.length; i += 1) {
    if (text[i] === '\\') { i += 1; continue; }
    if (text[i] === '[') depth += 1;
    if (text[i] === ']' && --depth === 0) return i;
  }
  return -1;
}

export function extractMarkdownLinks(source) {
  const definitions = new Map();
  let text = withoutCodeBlocks(source);
  text = text.replace(/^ {0,3}(?:>[ \t]?)* {0,3}\[([^\]\n]+)\]:[ \t]*(?:\n[ \t]*)?([^\n]+)/gmu, (definition, label, rest) => {
    const target = destinationAt(rest, 0);
    const title = target ? rest.slice(target.end).trim() : '';
    // An invalid definition is ordinary rendered prose, so keep its links visible.
    if (!target?.destination || (title && !/^(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|\((?:[^)\\]|\\.)*\))$/u.test(title))) return definition;
    if (!definitions.has(referenceLabel(label))) definitions.set(referenceLabel(label), target.destination);
    return blank(definition);
  });
  const links = [];
  const referenceSuffixes = new Set();
  const destinations = new Map();
  const codeSpans = [];
  const add = (destination, offset) => links.push({ destination: decodeEntities(unescapeMarkdown(destination)), line: text.slice(0, offset).split('\n').length });
  for (let i = 0; i < text.length; i += 1) {
    if (destinations.has(i)) { i = destinations.get(i); continue; }
    if (text[i] === '\\') { i += 1; continue; }
    if (text[i] === '`') {
      const end = inlineCodeEnd(text, i);
      if (text.slice(i, end).includes('\n') || /[^`]/u.test(text.slice(i, end))) codeSpans.push([i, end]);
      i = end - 1;
      continue;
    }
    if (text[i] !== '[' || referenceSuffixes.has(i)) continue;
    const end = closingBracket(text, i);
    if (end < 0) continue;
    const label = text.slice(i + 1, end);
    if (text[end + 1] === '(') {
      const target = destinationAt(text, end + 2);
      if (!target) continue;
      let cursor = target.end;
      while (/\s/u.test(text[cursor] ?? '') && cursor < text.length) cursor += 1;
      if ('"\'('.includes(text[cursor] ?? '\0')) {
        const close = text[cursor] === '(' ? ')' : text[cursor];
        cursor += 1;
        while (cursor < text.length && text[cursor] !== close) cursor += text[cursor] === '\\' ? 2 : 1;
        cursor += 1;
        while (/\s/u.test(text[cursor] ?? '') && cursor < text.length) cursor += 1;
      }
      if (text[cursor] === ')') {
        add(target.destination, i);
        // Backticks in a link destination are URL characters, not code spans.
        destinations.set(end + 1, cursor);
      }
    } else {
      let reference = label;
      if (text[end + 1] === '[') {
        const referenceEnd = closingBracket(text, end + 1);
        if (referenceEnd < 0) continue;
        reference = text.slice(end + 2, referenceEnd) || label;
        referenceSuffixes.add(end + 1);
      }
      const destination = definitions.get(referenceLabel(reference));
      if (destination !== undefined) add(destination, i);
    }
  }
  for (const tag of text.matchAll(/<(?:a|img|source|video|audio)\b[^>]*>/giu)) {
    if (codeSpans.some(([start, end]) => tag.index >= start && tag.index < end)) continue;
    for (const attribute of tag[0].matchAll(/\s(?:href|src)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/giu)) add(attribute[1] ?? attribute[2] ?? attribute[3], tag.index);
  }
  return links;
}

function decodeEntities(value) {
  return value.replace(/&(?:amp|lt|gt|quot|apos|#\d+|#x[\da-f]+);/giu, (entity) => {
    const named = { '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&apos;': "'" };
    if (named[entity.toLowerCase()]) return named[entity.toLowerCase()];
    const point = entity[2].toLowerCase() === 'x' ? Number.parseInt(entity.slice(3, -1), 16) : Number(entity.slice(2, -1));
    return point <= 0x10ffff ? String.fromCodePoint(point) : entity;
  });
}

export function markdownAnchors(source) {
  const anchors = new Set();
  const text = withoutCodeBlocks(source);
  for (const match of text.matchAll(/<(?:a|h[1-6]|span|div)\b[^>]*\b(?:id|name)\s*=\s*["']([^"']+)["'][^>]*>/giu)) anchors.add(decodeEntities(match[1]));
  const addHeading = (heading) => {
    const base = decodeEntities(unescapeMarkdown(heading.replace(/!?\[([^\]]+)\]\([^)]*\)/gu, '$1').replace(/<[^>]*>/gu, '')))
      .toLowerCase().trim().replace(/[`*~]/gu, '').replace(/[^\p{L}\p{N}\p{M}\s_-]/gu, '').replace(/\s/gu, '-');
    let anchor = base;
    let count = 0;
    while (anchors.has(anchor)) anchor = `${base}-${++count}`;
    anchors.add(anchor);
  };
  const lines = text.split('\n');
  for (let index = 0; index < lines.length; index += 1) {
    const match = /^ {0,3}#{1,6}(?:\s+(.+?)\s*#*\s*|\s*)$/u.exec(lines[index]);
    if (match) addHeading(match[1] ?? '');
    else if (index > 0 && /^ {0,3}(?:=+|-+)\s*$/u.test(lines[index]) && lines[index - 1].trim() && !/^\s*(?:[|#>-]|[-*+]\s)/u.test(lines[index - 1])) addHeading(lines[index - 1].trim());
  }
  return anchors;
}

export function checkLinks(root = repoRoot, options = {}) {
  const files = options.files ?? walkFiles('.', root).filter((file) => /\.md$/iu.test(file));
  const published = options.publishedFiles ? new Set(options.publishedFiles) : null;
  const errors = [];
  let links = 0;
  const anchorsCache = new Map();
  const insideRoot = (target) => target !== '..' && !target.startsWith('../') && !path.isAbsolute(target);
  for (const file of files) {
    for (const { destination, line } of extractMarkdownLinks(readText(file, root))) {
      if (!destination || /^(?:[a-z][a-z\d+.-]*:|\/\/)/iu.test(destination)) continue;
      links += 1;
      const hashIndex = destination.indexOf('#');
      const rawTarget = (hashIndex >= 0 ? destination.slice(0, hashIndex) : destination).split('?', 1)[0];
      const rawAnchor = hashIndex >= 0 ? destination.slice(hashIndex + 1) : '';
      let decodedTarget;
      let decodedAnchor;
      try { decodedTarget = decodeURIComponent(rawTarget); decodedAnchor = decodeURIComponent(rawAnchor); }
      catch { errors.push(`${file}:${line}: link is not valid percent-encoding: ${destination}`); continue; }
      const absolute = decodedTarget.startsWith('/') ? path.resolve(root, `.${decodedTarget}`) : path.resolve(root, path.dirname(file), decodedTarget || path.basename(file));
      const targetRelative = toPosix(path.relative(root, absolute));
      if (!insideRoot(targetRelative) || !fs.existsSync(absolute)) {
        errors.push(`${file}:${line}: missing relative link target ${destination}`);
        continue;
      }
      if (!insideRoot(toPosix(path.relative(fs.realpathSync(root), fs.realpathSync(absolute))))) {
        errors.push(`${file}:${line}: link target escapes repository: ${destination}`);
        continue;
      }
      const directory = fs.statSync(absolute).isDirectory();
      if (published && !(directory ? [...published].some((entry) => entry.startsWith(`${targetRelative ? `${targetRelative}/` : ''}`)) : published.has(targetRelative))) {
        errors.push(`${file}:${line}: link target is not in the published file inventory: ${destination}`);
        continue;
      }
      const markdownTarget = directory ? toPosix(path.join(targetRelative, 'README.md')) : targetRelative;
      const readmeExists = fs.existsSync(path.join(root, markdownTarget));
      if (directory && decodedAnchor && readmeExists && published && !published.has(markdownTarget)) {
        errors.push(`${file}:${line}: directory anchor target is not in the published file inventory: ${destination}`);
        continue;
      }
      if (directory && options.requireDirectoryReadme !== false && !readmeExists) errors.push(`${file}:${line}: linked directory has no README.md: ${destination}`);
      if (decodedAnchor && /\.md$/iu.test(markdownTarget) && readmeExists) {
        if (!anchorsCache.has(markdownTarget)) anchorsCache.set(markdownTarget, markdownAnchors(readText(markdownTarget, root)));
        if (!anchorsCache.get(markdownTarget).has(decodedAnchor)) errors.push(`${file}:${line}: missing anchor #${decodedAnchor} in ${markdownTarget}`);
      } else if (directory && decodedAnchor) errors.push(`${file}:${line}: anchor has no directory README.md: ${destination}`);
    }
  }
  if (errors.length) throw new Error(`documentation link check failed:\n${errors.map((error) => `- ${error}`).join('\n')}`);
  return { files: files.length, relativeLinks: links };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) process.stdout.write(`${JSON.stringify(checkLinks())}\n`);
