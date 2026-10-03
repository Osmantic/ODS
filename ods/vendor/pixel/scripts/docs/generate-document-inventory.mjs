import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  generatedNotice,
  markdownEscape,
  modeFromArgs,
  repoRoot,
  walkFiles,
  writeGenerated,
} from './lib.mjs';

function classify(file) {
  if (file.startsWith('docs/')) return { category: 'Maintained product documentation', boundary: 'Current indexed guide or generated reference; feature status remains page-specific.' };
  if (/^LIVE-AUDIT(?:-|\.md)/u.test(file) || file === 'DREAM-FORGE-SOURCE-AUDIT.md') return { category: 'Release or historical evidence', boundary: 'Point-in-time evidence only; read exact source identity and current compatibility status.' };
  if (/(^|\/)(?:[^/]*PLAN[^/]*|ROADMAP|PRODUCTIZATION)\.md$/iu.test(file)) return { category: 'Plan or program record', boundary: 'Intent/history; not current runtime or release authority.' };
  if (file.startsWith('workspace-template/')) return { category: 'Installed workspace/template content', boundary: 'Runtime/template input; edit only through its owning template contract.' };
  if (file.includes('/README.md')) return { category: 'Colocated component guide', boundary: 'Component-maintainer scope; not a whole-product support claim.' };
  if (!file.includes('/')) return { category: 'Path-stable root contract or entry point', boundary: 'Canonical or specialist source; pair with generated status and job-oriented docs.' };
  return { category: 'Colocated specialist document', boundary: 'Owning subsystem scope; verify its feature/evidence status before use.' };
}

export function buildDocumentInventory(root = repoRoot) {
  return walkFiles('.', root)
    .filter((file) => file.endsWith('.md') && file !== 'docs/reference/document-inventory.md')
    .map((file) => ({ path: file, ...classify(file) }))
    .sort((left, right) => left.path.localeCompare(right.path));
}

export function renderDocumentInventory(documents) {
  const groups = new Map();
  for (const document of documents) {
    if (!groups.has(document.category)) groups.set(document.category, []);
    groups.get(document.category).push(document);
  }
  const sections = [...groups].map(([category, entries]) => `## ${category}\n\n| Document | Reading boundary |\n|---|---|\n${entries.map((entry) => `| [\`${markdownEscape(entry.path)}\`](../../${entry.path}) | ${markdownEscape(entry.boundary)} |`).join('\n')}`).join('\n\n');
  return `---
title: Pixel documentation inventory
doc_type: reference
audience: [owner, operator, contributor, security-reviewer, maintainer]
feature_status: mixed
owners: [documentation]
sources_of_truth: [scripts/docs/inventory-baseline.json]
generated_by: scripts/docs/generate-document-inventory.mjs
last_verified_at: generated
---

${generatedNotice}

# Pixel documentation inventory

This inventory makes **${documents.length} existing Markdown files** discoverable without moving path-sensitive release evidence, root contracts, component guides, or installed template content. Classification is a reading boundary, not a claim that every older document is current. Start with the [job-oriented documentation hub](../README.md) and [generated status](../status.md); use this catalog when tracing specialist or historical source material.

${sections}
`;
}

export function generateDocumentInventory(mode, root = repoRoot) {
  const documents = buildDocumentInventory(root);
  writeGenerated('docs/reference/document-inventory.md', renderDocumentInventory(documents), mode, root);
  return documents;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) generateDocumentInventory(modeFromArgs());
