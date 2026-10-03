import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { generatedNotice, markdownEscape, modeFromArgs, readJson, readText, repoRoot, walkFiles, writeGenerated } from './lib.mjs';

function schemaFamily(filename) {
  if (filename.startsWith('control-')) return 'Owner control';
  if (filename.startsWith('release-') || filename.startsWith('promotion-') || filename.startsWith('qualification-') || filename.startsWith('runtime-attestation')) return 'Release and qualification';
  if (filename.startsWith('frontier-')) return 'Frontier';
  if (filename.startsWith('operations-') || filename.startsWith('external-action') || filename.startsWith('github-action')) return 'Operations and external actions';
  if (filename.startsWith('portal-') || filename.startsWith('agent-comparison')) return 'Evaluation harness';
  if (filename.startsWith('work-') || filename.startsWith('deep-work-')) return 'Deep Work and local work';
  return 'Core, compatibility, and customization';
}

const familyDetails = {
  'Owner control': { owner: 'control server/UI and control action/configuration tooling', guide: '../use/control-surface.md' },
  'Release and qualification': { owner: 'release, update, compatibility, and qualification tooling', guide: '../releases/qualification.md' },
  Frontier: { owner: 'Frontier plugin, broker, policy, and qualification tooling', guide: '../use/frontier-review.md' },
  'Operations and external actions': { owner: 'source/action journals, Operations plugin/broker/runner, and actuators', guide: '../use/operations.md' },
  'Evaluation harness': { owner: 'portal outcome, comparison, sealed-corpus, and qualification harnesses', guide: '../security/evaluations.md' },
  'Deep Work and local work': { owner: 'work controller, broker, runner, provider, and inert owner-control tooling', guide: '../use/deep-work.md' },
  'Core, compatibility, and customization': { owner: 'configuration, client/limb kits, compatibility, and core contracts', guide: '../extend/choose-an-extension-point.md' },
};

function referenceCorpus(root) {
  return walkFiles('.', root)
    .filter((file) => !file.startsWith('schemas/') && !file.startsWith('docs/') && /\.(?:js|mjs|py|sh|json|md)$/u.test(file))
    .map((file) => {
      try { return { file, text: readText(file, root) }; } catch { return null; }
    })
    .filter(Boolean);
}

export function extractSchemas(root = repoRoot) {
  const corpus = referenceCorpus(root);
  return walkFiles('schemas', root)
    .filter((file) => file.endsWith('.schema.json'))
    .map((file) => {
      const schema = readJson(file, root);
      const basename = path.posix.basename(file);
      const references = corpus.filter((entry) => entry.text.includes(basename)).map((entry) => entry.file);
      const codeReferences = references.filter((entry) => !entry.startsWith('tests/') && !entry.startsWith('security-evals/')).slice(0, 3);
      const testReferences = references.filter((entry) => entry.startsWith('tests/') || entry.startsWith('security-evals/')).slice(0, 2);
      const family = schemaFamily(basename);
      return {
        path: file,
        family,
        ownerSurface: familyDetails[family].owner,
        guide: familyDetails[family].guide,
        id: schema.$id ?? null,
        title: schema.title ?? null,
        description: schema.description ?? null,
        schemaVersionConst: schema.properties?.schemaVersion?.const ?? null,
        codeReferences,
        testReferences,
      };
    })
    .sort((left, right) => left.path.localeCompare(right.path));
}

export function renderSchemas(schemas) {
  const groups = new Map();
  for (const schema of schemas) {
    if (!groups.has(schema.family)) groups.set(schema.family, []);
    groups.get(schema.family).push(schema);
  }
  const sections = [...groups].map(([family, entries]) => `## ${family}\n\n**Producer/consumer surface:** ${markdownEscape(familyDetails[family].owner)}. [Owning guide](${familyDetails[family].guide}).\n\n| Schema | Purpose | Version | Source references | Test/eval references |\n|---|---|---|---|---|\n${entries.map((entry) => `| [\`${path.posix.basename(entry.path)}\`](../../${entry.path}) | ${markdownEscape(entry.title ?? entry.description ?? 'No title or description declared')} | ${entry.schemaVersionConst ?? 'not declared as a const'} | ${entry.codeReferences.length ? entry.codeReferences.map((file) => `[\`${markdownEscape(file)}\`](../../${file})`).join(', ') : 'no direct non-test basename reference found'} | ${entry.testReferences.length ? entry.testReferences.map((file) => `[\`${markdownEscape(file)}\`](../../${file})`).join(', ') : 'no direct test/eval basename reference found'} |`).join('\n')}`).join('\n\n');
  return `---
title: Pixel schema catalog
doc_type: reference
audience: [contributor, security-reviewer, maintainer]
feature_status: mixed
owners: [documentation, architecture]
sources_of_truth: [schemas/]
generated_by: scripts/docs/generate-schema-catalog.mjs
last_verified_at: generated
---

${generatedNotice}

# Pixel schema catalog

This catalog covers all **${schemas.length}** JSON Schema files in \`schemas/\`. A schema proves that a contract shape is defined; it does not prove the corresponding feature is enabled, Supported, qualified, deployed, or live-used.

The family labels are navigation aids derived from filenames. Producer/consumer surfaces are family ownership boundaries; source/test links are generated from exact schema-basename references and are examples, not an exhaustive call graph. The schema itself remains authoritative.

${sections}
`;
}

export function generateSchemaCatalog(mode, root = repoRoot) {
  const schemas = extractSchemas(root);
  const baseline = readJson('scripts/docs/inventory-baseline.json', root);
  if (schemas.length !== baseline.counts.jsonSchemas) throw new Error(`schema inventory changed: found ${schemas.length}; review and update scripts/docs/inventory-baseline.json`);
  writeGenerated('docs/reference/schemas.md', renderSchemas(schemas), mode, root);
  writeGenerated('docs/reference/schemas.json', JSON.stringify({ schemaVersion: 1, generatedFrom: 'schemas/*.schema.json', schemas }, null, 2), mode, root);
  return schemas;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) generateSchemaCatalog(modeFromArgs());
