import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseFrontMatter, readJson, readText, repoRoot, walkFiles } from './lib.mjs';

export function checkSourceMap(root = repoRoot) {
  const map = readJson('scripts/docs/source-map.json', root);
  const errors = [];
  const mappedDocuments = new Set();
  const mappedSources = new Set();
  for (const entry of map.entries ?? []) {
    if (mappedSources.has(entry.source)) errors.push(`duplicate source-map entry: ${entry.source}`);
    mappedSources.add(entry.source);
    if (!fs.existsSync(path.join(root, entry.source))) errors.push(`source does not exist: ${entry.source}`);
    for (const document of entry.documents ?? []) {
      mappedDocuments.add(document);
      if (!fs.existsSync(path.join(root, document))) {
        errors.push(`mapped document does not exist: ${document}`);
        continue;
      }
      const { metadata } = parseFrontMatter(readText(document, root), document);
      const covered = (metadata.sources_of_truth ?? []).some((source) => entry.source === source || entry.source.startsWith(`${source.replace(/\/$/u, '')}/`) || source.startsWith(`${entry.source.replace(/\/$/u, '')}/`));
      if (!covered) errors.push(`${document}: metadata does not cover mapped source ${entry.source}`);
    }
  }
  for (const document of walkFiles('docs', root).filter((file) => file.endsWith('.md'))) {
    if (!mappedDocuments.has(document)) errors.push(`documentation page has no source-map owner: ${document}`);
  }
  if (errors.length) throw new Error(`documentation source-map check failed:\n${errors.map((error) => `- ${error}`).join('\n')}`);
  return { mappings: map.entries.length };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) process.stdout.write(`${JSON.stringify(checkSourceMap())}\n`);
