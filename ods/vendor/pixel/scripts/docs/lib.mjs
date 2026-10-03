import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
export const generatedNotice = '<!-- GENERATED FILE. Run `node scripts/docs/generate.mjs --write`; do not edit by hand. -->';

export function toPosix(value) {
  return value.split(path.sep).join('/');
}

export function readText(relativePath, root = repoRoot) {
  return fs.readFileSync(path.join(root, relativePath), 'utf8').replace(/\r\n/g, '\n');
}

export function readJson(relativePath, root = repoRoot) {
  try {
    return JSON.parse(readText(relativePath, root));
  } catch (error) {
    throw new Error(`${relativePath}: invalid JSON: ${error.message}`);
  }
}

export function walkFiles(relativeDirectory = '.', root = repoRoot) {
  const absoluteDirectory = path.join(root, relativeDirectory);
  if (!fs.existsSync(absoluteDirectory)) return [];
  const results = [];
  const visit = (absolute, relative) => {
    for (const entry of fs.readdirSync(absolute, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      if (entry.name === '.git' || entry.name === 'node_modules') continue;
      const childAbsolute = path.join(absolute, entry.name);
      const childRelative = relative === '.' ? entry.name : path.join(relative, entry.name);
      if (entry.isDirectory()) visit(childAbsolute, childRelative);
      else if (entry.isFile()) results.push(toPosix(childRelative));
    }
  };
  visit(absoluteDirectory, relativeDirectory);
  return results;
}

export function ensureFinalNewline(value) {
  return `${value.replace(/\s+$/u, '')}\n`;
}

export function writeGenerated(relativePath, content, mode, root = repoRoot) {
  const normalized = ensureFinalNewline(content);
  const absolutePath = path.join(root, relativePath);
  if (mode === 'check') {
    if (!fs.existsSync(absolutePath)) throw new Error(`${relativePath}: generated file is missing`);
    const current = readText(relativePath, root);
    if (current !== normalized) throw new Error(`${relativePath}: generated file is stale; run node scripts/docs/generate.mjs --write`);
    return;
  }
  fs.mkdirSync(path.dirname(absolutePath), { recursive: true });
  fs.writeFileSync(absolutePath, normalized, 'utf8');
}

export function modeFromArgs(args = process.argv.slice(2)) {
  const modes = args.filter((arg) => arg === '--check' || arg === '--write');
  if (modes.length !== 1) throw new Error('exactly one of --check or --write is required');
  return modes[0].slice(2);
}

export function parseFrontMatter(text, sourceName) {
  if (!text.startsWith('---\n')) throw new Error(`${sourceName}: missing YAML front matter`);
  const end = text.indexOf('\n---\n', 4);
  if (end < 0) throw new Error(`${sourceName}: unterminated YAML front matter`);
  const object = {};
  for (const [index, rawLine] of text.slice(4, end).split('\n').entries()) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) continue;
    const match = /^([a-z][a-z0-9_]*)\s*:\s*(.*)$/u.exec(line);
    if (!match) throw new Error(`${sourceName}:${index + 2}: unsupported front-matter syntax`);
    const [, key, rawValue] = match;
    if (Object.hasOwn(object, key)) throw new Error(`${sourceName}: duplicate front-matter key ${key}`);
    let value = rawValue.trim();
    if (value.startsWith('[')) {
      if (!value.endsWith(']')) throw new Error(`${sourceName}: multiline arrays are not allowed for ${key}`);
      value = value.slice(1, -1).trim();
      object[key] = value ? value.split(',').map((item) => item.trim().replace(/^['"]|['"]$/g, '')) : [];
    } else {
      object[key] = value.replace(/^['"]|['"]$/g, '');
    }
  }
  return { metadata: object, body: text.slice(end + 5) };
}

export function markdownEscape(value) {
  return String(value).replace(/\\/g, '\\\\').replace(/\|/g, '\\|').replace(/\r?\n/g, ' ');
}

export function relativeLink(fromFile, toFile) {
  const link = toPosix(path.relative(path.dirname(fromFile), toFile));
  return link || path.basename(toFile);
}

export function semverParts(value) {
  const match = /^(\d+)\.(\d+)\.(\d+)$/.exec(value);
  if (!match) throw new Error(`invalid semantic version: ${value}`);
  return match.slice(1).map(Number);
}

export function compareSemver(left, right) {
  const a = semverParts(left);
  const b = semverParts(right);
  for (let index = 0; index < 3; index += 1) {
    if (a[index] !== b[index]) return a[index] - b[index];
  }
  return 0;
}
