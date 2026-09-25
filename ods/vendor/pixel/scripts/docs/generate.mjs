import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { modeFromArgs, repoRoot } from './lib.mjs';
import { generateCliReference } from './generate-cli-reference.mjs';
import { generateConfigReference } from './generate-config-reference.mjs';
import { generateDocumentInventory } from './generate-document-inventory.mjs';
import { generatePlatformReference } from './generate-platform-reference.mjs';
import { generateReleaseEvidenceIndex } from './generate-release-evidence-index.mjs';
import { generateSchemaCatalog } from './generate-schema-catalog.mjs';
import { generateStatus } from './generate-status.mjs';

export function generateAll(mode, root = repoRoot) {
  const status = generateStatus(mode, root);
  const commands = generateCliReference(mode, root);
  const schemas = generateSchemaCatalog(mode, root);
  const examples = generateConfigReference(mode, root);
  const releaseEvidence = generateReleaseEvidenceIndex(mode, root);
  const platformReference = generatePlatformReference(mode, root);
  const documentInventory = generateDocumentInventory(mode, root);
  return {
    status,
    commands: commands.length,
    schemas: schemas.length,
    configurationExamples: examples.length,
    releaseEvidenceRows: releaseEvidence.rows.length,
    unreferencedReleaseAudits: releaseEvidence.unreferencedAudits.length,
    servicePathEntries: platformReference.locations,
    hostLanes: platformReference.hostLanes,
    indexedMarkdownDocuments: documentInventory.length,
  };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const summary = generateAll(modeFromArgs());
  process.stdout.write(`${JSON.stringify(summary, null, 2)}\n`);
}
