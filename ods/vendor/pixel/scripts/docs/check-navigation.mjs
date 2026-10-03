import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { readText, repoRoot, toPosix, walkFiles } from './lib.mjs';

function withoutFencedCode(text) {
  let fenced = false;
  return text.split('\n').map((line) => {
    if (/^\s*(```|~~~)/u.test(line)) {
      fenced = !fenced;
      return '';
    }
    return fenced ? '' : line;
  }).join('\n');
}

function documentLinks(file, root, docs) {
  const links = new Set();
  for (const match of withoutFencedCode(readText(file, root)).matchAll(/\[[^\]]*\]\(([^)]+)\)/gu)) {
    let destination = match[1].trim().replace(/^<|>$/g, '').split(/\s+["']/u, 1)[0];
    if (!destination || /^(?:https?:|mailto:|tel:|data:|#)/iu.test(destination)) continue;
    destination = destination.split('#', 1)[0];
    let decoded;
    try { decoded = decodeURIComponent(destination); } catch { continue; }
    let target = toPosix(path.normalize(path.join(path.dirname(file), decoded)));
    const absolute = path.join(root, target);
    if (fs.existsSync(absolute) && fs.statSync(absolute).isDirectory()) target = toPosix(path.join(target, 'README.md'));
    if (docs.has(target)) links.add(target);
  }
  return links;
}

export function checkNavigation(root = repoRoot) {
  const files = walkFiles('docs', root).filter((file) => file.endsWith('.md'));
  const docs = new Set(files);
  const start = 'docs/README.md';
  if (!docs.has(start)) throw new Error('documentation navigation check failed: docs/README.md is missing');
  const reachable = new Set([start]);
  const queue = [start];
  while (queue.length) {
    const file = queue.shift();
    for (const target of documentLinks(file, root, docs)) {
      if (reachable.has(target)) continue;
      reachable.add(target);
      queue.push(target);
    }
  }
  const unreachable = files.filter((file) => !reachable.has(file));
  if (unreachable.length) throw new Error(`documentation navigation check failed:\n${unreachable.map((file) => `- not reachable from docs/README.md: ${file}`).join('\n')}`);
  return { files: files.length, reachable: reachable.size };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) process.stdout.write(`${JSON.stringify(checkNavigation())}\n`);
