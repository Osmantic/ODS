import fs from 'node:fs';
import path from 'node:path';
import { markdownEscape, readJson, repoRoot } from './lib.mjs';

// An export omission is a disclosure boundary, never release qualification.
export function documentAvailability(document, root = repoRoot) {
  const absolute = path.resolve(root, document);
  const relative = path.relative(root, absolute);
  if (relative.startsWith('..') || path.isAbsolute(relative)) {
    throw new Error(`evidence document leaves repository: ${document}`);
  }
  if (fs.existsSync(absolute) && fs.statSync(absolute).isFile()) return true;
  const exportFile = path.join(root, 'PUBLIC-SOURCE-EXPORT.json');
  if (fs.existsSync(exportFile)) {
    const record = readJson('PUBLIC-SOURCE-EXPORT.json', root);
    if (record.schemaVersion === 1 && record.omittedDocuments?.[document]) return false;
  }
  throw new Error(`missing evidence document without a declared export omission: ${document}`);
}

export function evidenceReference(document, target, available, label = document) {
  return available
    ? `[${markdownEscape(label)}](${target})`
    : `\`${markdownEscape(document)}\` (not included in public export; evidence not verified here)`;
}
