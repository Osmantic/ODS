import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { readJson, repoRoot, walkFiles } from './lib.mjs';

const durableRootDocs = new Set(['README.md', 'CHANGELOG.md', 'CONTRIBUTING.md', 'SECURITY.md', 'SUPPORT.md', 'LICENSE.md', 'THIRD_PARTY_NOTICES.md']);

export function checkRootPolicy(root = repoRoot) {
  const baseline = readJson('scripts/docs/inventory-baseline.json', root);
  const allowed = new Set([...baseline.rootMarkdownAtBaseline, ...durableRootDocs]);
  const rootMarkdown = walkFiles('.', root).filter((file) => !file.includes('/') && file.endsWith('.md'));
  const unauthorized = rootMarkdown.filter((file) => !allowed.has(file));
  if (unauthorized.length) throw new Error(`new root Markdown requires an explicit reviewed exception or a docs/ path:\n${unauthorized.map((file) => `- ${file}`).join('\n')}`);
  return { rootMarkdown: rootMarkdown.length, baselineMarkdown: baseline.rootMarkdownAtBaseline.length };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) process.stdout.write(`${JSON.stringify(checkRootPolicy())}\n`);
