import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { generatedNotice, markdownEscape, modeFromArgs, readJson, readText, repoRoot, walkFiles, writeGenerated } from './lib.mjs';

function isCanonicalExample(file) {
  return file === '.env.example' || /(^|\/)([^/]+\.)?example\.(json|ya?ml|env)$/u.test(file);
}

function classify(file) {
  if (file === '.env.example') return {
    classification: 'generated-output template',
    custody: 'repository example; generated live file is private',
    application: 'rerun configure, review plan, then apply; do not hand-edit generated live output',
  };
  if (file === 'onboarding.example.json' || file === 'client-overlay.example.json') return {
    classification: 'owner-authored input template',
    custody: 'copy to an owner-private path before adding real values',
    application: 'validate and regenerate; review the resulting plan before apply',
  };
  if (file.startsWith('control/')) return {
    classification: 'private control-policy input template',
    custody: 'owner-private installed-host input',
    application: 'validate, then reconfigure/restart only the owning control component',
  };
  if (file.startsWith('deploy/') && /(agent-comparison|qualification|campaign|maintenance)/u.test(file)) return {
    classification: 'qualification or evaluation input template',
    custody: 'disposable/private evaluation state',
    application: 'starts a new exact evaluation only; grants no runtime or promotion authority',
  };
  if (file.startsWith('deploy/')) return {
    classification: 'private component-policy input template',
    custody: 'broker/operator-private state outside Git after customization',
    application: 'validate and use the owning installer/reconfigure path; file presence grants no authority',
  };
  if (file === 'OPENCLAW-UPSTREAM-REVIEW.example.json') return {
    classification: 'repository review-contract template',
    custody: 'candidate branch after exact source binding',
    application: 'upstream review workflow only; not runtime configuration',
  };
  return {
    classification: 'repository contract example',
    custody: 'repository template; private values prohibited',
    application: 'follow the owning component or release workflow',
  };
}

function summarize(file, root) {
  if (file.endsWith('.json')) {
    const value = readJson(file, root);
    const keys = value && typeof value === 'object' && !Array.isArray(value) ? Object.keys(value).sort() : [];
    return { format: 'JSON', fields: keys };
  }
  const text = readText(file, root);
  const fields = [...text.matchAll(/^([A-Z][A-Z0-9_]*)=/gmu)].map((match) => match[1]);
  if (file.endsWith('.env') || file.endsWith('.env.example') || fields.length) return { format: 'environment', fields: [...new Set(fields)].sort() };
  return { format: path.extname(file).slice(1).toUpperCase() || 'text', fields: [] };
}

export function extractConfigExamples(root = repoRoot) {
  return walkFiles('.', root)
    .filter(isCanonicalExample)
    .map((file) => ({ path: file, ...classify(file), ...summarize(file, root) }))
    .sort((left, right) => left.path.localeCompare(right.path));
}

export function renderConfig(examples, ancillary) {
  return `---
title: Pixel configuration source map
doc_type: reference
audience: [owner, operator, contributor, security-reviewer]
feature_status: mixed
owners: [documentation, configuration]
sources_of_truth: [.env.example, onboarding.example.json, configs/, control/, deploy/]
generated_by: scripts/docs/generate-config-reference.mjs
last_verified_at: generated
---

${generatedNotice}

# Pixel configuration source map

This catalog covers the audited **${examples.length} canonical JSON, YAML, and environment examples**. It distinguishes authored inputs, generated output shapes, private component policy, evaluation inputs, and repository contracts. It does not make every field safe to edit or every component enabled.

## Configuration lifecycle

Owner-authored onboarding or overlay input is validated before Pixel generates private configuration. A reviewed plan precedes apply. Installed runtime state and content-free evidence are outputs, not configuration inputs. Changing a private component policy requires that component's validated reconfigure/restart path; copying an example into place never grants capability or authority.

| Example | Classification | Custody | Apply/reload boundary | Format | Top-level fields or environment keys |
|---|---|---|---|---|---|
${examples.map((entry) => `| [\`${entry.path}\`](../../${entry.path}) | ${markdownEscape(entry.classification)} | ${markdownEscape(entry.custody)} | ${markdownEscape(entry.application)} | ${entry.format} | ${entry.fields.length ? entry.fields.map((field) => `\`${markdownEscape(field)}\``).join(', ') : 'inspect the source contract'} |`).join('\n')}

## Additional templates outside the 42-file denominator

${ancillary.map((file) => `- [\`${file}\`](../../${file})`).join('\n')}

## Custody rule

Examples belong in the repository. Generated \`.env\` files, private policies, OAuth tokens, model keys, operator state, and evidence do not. Use the configuration workflow to create private state; never copy live credentials into examples or documentation. Defaults and constraints remain authoritative in the linked schemas and generator source; an example value is not a universal default.
`;
}

export function generateConfigReference(mode, root = repoRoot) {
  const examples = extractConfigExamples(root);
  const baseline = readJson('scripts/docs/inventory-baseline.json', root);
  if (examples.length !== baseline.counts.configurationExamples) throw new Error(`configuration-example inventory changed: found ${examples.length}; review and update scripts/docs/inventory-baseline.json`);
  for (const file of baseline.documentedAncillaryTemplates) {
    if (!walkFiles('.', root).includes(file)) throw new Error(`documented ancillary template is missing: ${file}`);
  }
  const payload = { schemaVersion: 1, generatedFrom: baseline.configurationExampleRule, examples, ancillaryTemplates: baseline.documentedAncillaryTemplates };
  writeGenerated('docs/reference/configuration.md', renderConfig(examples, baseline.documentedAncillaryTemplates), mode, root);
  writeGenerated('docs/reference/configuration.json', JSON.stringify(payload, null, 2), mode, root);
  return examples;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) generateConfigReference(modeFromArgs());
