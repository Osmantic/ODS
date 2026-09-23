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

function slug(value) {
  return value.toLowerCase().trim().replace(/[`*_~]/g, '').replace(/[^\p{L}\p{N}\s-]/gu, '').replace(/\s+/g, '-').replace(/-+/g, '-');
}

function anchorsFor(relativePath, root) {
  const anchors = new Set();
  const counts = new Map();
  for (const line of withoutFencedCode(readText(relativePath, root)).split('\n')) {
    const match = /^#{1,6}\s+(.+?)\s*#*$/u.exec(line);
    if (!match) continue;
    const base = slug(match[1]);
    const count = counts.get(base) ?? 0;
    anchors.add(count === 0 ? base : `${base}-${count}`);
    counts.set(base, count + 1);
  }
  return anchors;
}

export function checkLinks(root = repoRoot) {
  const files = walkFiles('.', root).filter((file) => file.endsWith('.md'));
  const errors = [];
  let links = 0;
  const anchorsCache = new Map();
  for (const file of files) {
    const text = withoutFencedCode(readText(file, root));
    for (const match of text.matchAll(/\[[^\]]*\]\(([^)]+)\)/gu)) {
      let destination = match[1].trim().replace(/^<|>$/g, '').split(/\s+["']/u, 1)[0];
      if (!destination || /^(?:https?:|mailto:|tel:|data:)/iu.test(destination)) continue;
      links += 1;
      const hashIndex = destination.indexOf('#');
      const rawTarget = hashIndex >= 0 ? destination.slice(0, hashIndex) : destination;
      const rawAnchor = hashIndex >= 0 ? destination.slice(hashIndex + 1) : '';
      let decodedTarget;
      let decodedAnchor;
      try {
        decodedTarget = decodeURIComponent(rawTarget);
        decodedAnchor = decodeURIComponent(rawAnchor);
      } catch {
        errors.push(`${file}: link is not valid percent-encoding: ${destination}`);
        continue;
      }
      const targetRelative = decodedTarget ? toPosix(path.normalize(path.join(path.dirname(file), decodedTarget))) : file;
      const targetAbsolute = path.join(root, targetRelative);
      if (!fs.existsSync(targetAbsolute)) {
        errors.push(`${file}: missing relative link target ${destination}`);
        continue;
      }
      const stat = fs.statSync(targetAbsolute);
      const markdownTarget = stat.isDirectory() ? toPosix(path.join(targetRelative, 'README.md')) : targetRelative;
      if (stat.isDirectory() && !fs.existsSync(path.join(root, markdownTarget))) errors.push(`${file}: linked directory has no README.md: ${destination}`);
      if (decodedAnchor && markdownTarget.endsWith('.md') && fs.existsSync(path.join(root, markdownTarget))) {
        if (!anchorsCache.has(markdownTarget)) anchorsCache.set(markdownTarget, anchorsFor(markdownTarget, root));
        if (!anchorsCache.get(markdownTarget).has(decodedAnchor.toLowerCase())) errors.push(`${file}: missing anchor #${decodedAnchor} in ${markdownTarget}`);
      }
    }
  }
  if (errors.length) throw new Error(`documentation link check failed:\n${errors.map((error) => `- ${error}`).join('\n')}`);
  return { files: files.length, relativeLinks: links };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) process.stdout.write(`${JSON.stringify(checkLinks())}\n`);
