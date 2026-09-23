import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { documentAvailability, evidenceReference } from '../scripts/docs/public-export.mjs';

test('only an explicitly recorded export omission can replace a missing evidence link', (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pixel-export-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  assert.throws(() => documentAvailability('audit.md', root), /missing evidence document/);
  fs.writeFileSync(path.join(root, 'PUBLIC-SOURCE-EXPORT.json'), JSON.stringify({
    schemaVersion: 1, omittedDocuments: { 'audit.md': 'Private operational evidence is excluded.' },
  }));
  assert.equal(documentAvailability('audit.md', root), false);
  assert.throws(() => documentAvailability('unreviewed.md', root), /missing evidence document/);
  assert.throws(() => documentAvailability('../outside.md', root), /leaves repository/);
  const omitted = evidenceReference('audit.md', '../audit.md', false);
  assert.match(omitted, /evidence not verified here/);
  assert.doesNotMatch(omitted, /\]\(/);
  fs.writeFileSync(path.join(root, 'audit.md'), '# Evidence\n');
  assert.equal(documentAvailability('audit.md', root), true);
  assert.equal(evidenceReference('audit.md', '../audit.md', true), '[audit.md](../audit.md)');
});
