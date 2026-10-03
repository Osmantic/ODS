import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseFrontMatter, readText, repoRoot, walkFiles } from './lib.mjs';

const allowedDocTypes = new Set(['tutorial', 'how-to', 'concept', 'reference', 'runbook', 'policy', 'assurance', 'release-evidence', 'plan', 'historical']);
const allowedAudiences = new Set(['owner', 'operator', 'contributor', 'security-reviewer', 'maintainer']);
const allowedStatuses = new Set(['supported', 'candidate', 'development-disabled', 'synthetic-only', 'historical', 'not-applicable', 'mixed']);
const requiredFields = ['title', 'doc_type', 'audience', 'feature_status', 'owners', 'sources_of_truth', 'last_verified_at'];

export function checkMetadata(root = repoRoot) {
  const files = walkFiles('docs', root).filter((file) => file.endsWith('.md'));
  const titles = new Map();
  const errors = [];
  for (const file of files) {
    let parsed;
    try {
      parsed = parseFrontMatter(readText(file, root), file);
    } catch (error) {
      errors.push(error.message);
      continue;
    }
    const { metadata } = parsed;
    for (const field of requiredFields) if (metadata[field] === undefined || metadata[field] === '' || (Array.isArray(metadata[field]) && metadata[field].length === 0)) errors.push(`${file}: missing ${field}`);
    if (!allowedDocTypes.has(metadata.doc_type)) errors.push(`${file}: invalid doc_type ${metadata.doc_type}`);
    if (!allowedStatuses.has(metadata.feature_status)) errors.push(`${file}: invalid feature_status ${metadata.feature_status}`);
    for (const audience of metadata.audience ?? []) if (!allowedAudiences.has(audience)) errors.push(`${file}: invalid audience ${audience}`);
    if (!Array.isArray(metadata.owners)) errors.push(`${file}: owners must be an array`);
    if (!Array.isArray(metadata.sources_of_truth)) errors.push(`${file}: sources_of_truth must be an array`);
    for (const source of metadata.sources_of_truth ?? []) {
      const absolute = path.join(root, source);
      if (!fs.existsSync(absolute)) errors.push(`${file}: source_of_truth does not exist: ${source}`);
    }
    if (metadata.last_verified_at === 'generated') {
      if (!readText(file, root).includes('GENERATED FILE')) errors.push(`${file}: generated verification date requires a generated-file notice`);
      if (!metadata.generated_by) errors.push(`${file}: generated pages must name generated_by`);
      else if (!fs.existsSync(path.join(root, metadata.generated_by))) errors.push(`${file}: generated_by does not exist: ${metadata.generated_by}`);
    } else if (!/^\d{4}-\d{2}-\d{2}$/u.test(metadata.last_verified_at ?? '')) {
      errors.push(`${file}: last_verified_at must be YYYY-MM-DD or generated`);
    }
    if (titles.has(metadata.title)) errors.push(`${file}: duplicate title also used by ${titles.get(metadata.title)}`);
    else titles.set(metadata.title, file);
  }
  if (errors.length) throw new Error(`documentation metadata check failed:\n${errors.map((error) => `- ${error}`).join('\n')}`);
  return { files: files.length };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) process.stdout.write(`${JSON.stringify(checkMetadata())}\n`);
