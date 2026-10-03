import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { generatedNotice, markdownEscape, modeFromArgs, readJson, readText, repoRoot, writeGenerated } from './lib.mjs';

export function extractCli(root = repoRoot) {
  const source = readText('pixel', root);
  const usageStart = source.indexOf('Commands:\n');
  const usageEnd = source.indexOf('\nEOF\n', usageStart);
  if (usageStart < 0 || usageEnd < 0) throw new Error('pixel: could not locate the Commands help block');
  const usage = new Map();
  for (const line of source.slice(usageStart + 10, usageEnd).split('\n')) {
    const match = /^  ([a-z0-9][a-z0-9-]*)\s+(.+)$/u.exec(line);
    if (!match) continue;
    if (usage.has(match[1])) throw new Error(`pixel: duplicate help entry ${match[1]}`);
    usage.set(match[1], match[2].trim());
  }
  const dispatch = new Map();
  const caseStart = source.indexOf('case "$command" in');
  const caseBody = source.slice(caseStart);
  const commandPattern = /^  ([a-z0-9][a-z0-9-]*)(?:\|[^)]*)?\)([\s\S]*?)(?=^  [a-z0-9][a-z0-9-]*(?:\|[^)]*)?\)|^  \*\)|^esac)/gmu;
  for (const match of caseBody.matchAll(commandPattern)) {
    const command = match[1];
    const target = /\$ROOT\/([^"\s]+)/u.exec(match[2])?.[1] ?? (command === 'help' ? 'pixel#usage' : 'pixel');
    if (dispatch.has(command)) throw new Error(`pixel: duplicate dispatch entry ${command}`);
    dispatch.set(command, target);
  }
  const usageOnly = [...usage.keys()].filter((command) => !dispatch.has(command));
  const dispatchOnly = [...dispatch.keys()].filter((command) => !usage.has(command));
  if (usageOnly.length || dispatchOnly.length) {
    throw new Error(`pixel help/dispatch mismatch; help-only=${usageOnly.join(',') || 'none'} dispatch-only=${dispatchOnly.join(',') || 'none'}`);
  }
  return [...usage].map(([command, description]) => ({ command, description, source: dispatch.get(command) }));
}

const families = [
  { id: 'install', name: 'Install, configure, and maintain', slug: 'install-configure-maintain', status: 'Mixed; installed-host state and each command contract control the claim.', guide: '../../operations/runbook.md' },
  { id: 'sources', name: 'Sources and external actions', slug: 'sources-external-actions', status: 'Mixed; projection, proposal, approval, and actuator authority remain separate.', guide: '../../use/sources-and-proposals.md' },
  { id: 'operations', name: 'Operations', slug: 'operations', status: 'Mixed; private policy, grants, approval, lease, and target identity bound authority.', guide: '../../use/operations.md' },
  { id: 'frontier', name: 'Frontier', slug: 'frontier', status: 'Mixed; a configured provider route still needs the applicable live qualification.', guide: '../../use/frontier-review.md' },
  { id: 'release', name: 'Qualification, release, and migration', slug: 'qualification-release-migration', status: 'Mixed; signatures, qualification, publication, staging, activation, and recovery are distinct authorities.', guide: '../../releases/README.md' },
  { id: 'customization', name: 'Customization', slug: 'customization', status: 'Mixed; status and authority depend on the selected extension seam.', guide: '../../extend/choose-an-extension-point.md' },
  { id: 'work', name: 'Deep Work and local work', slug: 'deep-work-local-work', status: 'Deep Work runtime is development-disabled; source-visible preparation and qualification commands do not enable it.', guide: '../../use/deep-work.md' },
];

function familyId(command) {
  if (command.startsWith('work-')) return 'work';
  if (command.startsWith('frontier-')) return 'frontier';
  if (command.startsWith('ops-')) return 'operations';
  if (command.startsWith('source-') || command === 'github-action' || command === 'action-journal') return 'sources';
  if (command.startsWith('release-') || command.startsWith('update-') || command.startsWith('outcome-') || command === 'promotion-status' || command === 'qualify-hosts' || command === 'upstream' || command === 'migrate-legacy-clean') return 'release';
  if (command === 'limb-kit' || command === 'client-kit' || command === 'extension-hash') return 'customization';
  return 'install';
}

const modeDependentConfirmation = new Set([
  'action-journal', 'client-kit', 'frontier-authority', 'frontier-budget', 'limb-kit',
  'migrate-legacy-clean', 'ops-authority', 'rotate', 'update-activate', 'update-cleanup',
  'update-reactivate', 'update-reactivation-recover', 'update-reactivation-rollback',
  'update-recover', 'upstream', 'work-goal-launch', 'work-model-backend', 'work-model-policy',
  'release-qualification-sign',
]);

function commandEffect(entry) {
  const description = entry.description.toLowerCase();
  if (modeDependentConfirmation.has(entry.command)) return 'mode-dependent; includes non-mutating and state-writing modes';
  if (entry.command === 'help' || /^(show|inspect)\b/u.test(description) || /\b(read-only|without writing|without executing|preview)\b/u.test(description)) return 'read-only or inert inspection';
  if (/\brequires --confirm\b/u.test(description) || /\b(apply|approve|install|enroll|pause|resume|restore|activate|rollback|execute|cancel|remove)\b/u.test(description)) return 'state-changing or authority-affecting';
  if (/\b(generate|build|create|draft|prepare|compile|snapshot|record|write|run|qualif|test|reconcile)\b/u.test(description)) return 'writes generated, private, evidence, or bounded task state';
  if (/\b(verify|check|status|usage|guidance|summarize)\b/u.test(description)) return 'inspection or verification; may emit bounded evidence';
  return 'may write state; inspect command help and owning guide';
}

function confirmation(entry, root) {
  if (entry.effect === 'read-only or inert inspection') return 'none for the named inspection/preview';
  if (/requires --confirm/iu.test(entry.description)) return 'required for the named mutation';
  let sourceDeclaresConfirmation = false;
  if (entry.source !== 'pixel#usage') {
    try { sourceDeclaresConfirmation = readText(entry.source, root).includes('--confirm'); } catch { sourceDeclaresConfirmation = false; }
  }
  if (modeDependentConfirmation.has(entry.command) || sourceDeclaresConfirmation) return 'mode/subcommand-dependent; inspect help';
  return 'not declared by root help; this is not permission to mutate';
}

function exitSemantics(command) {
  if (command === 'promotion-status' || command === 'outcome-compare' || command === 'outcome-campaign') return '0 pass; 3 valid non-pass/blocked; 2 contract or operational error';
  if (command === 'outcome-task-admit') return '0 admitted; 2 contract or operational error';
  return '0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting';
}

function enrich(commands, root) {
  return commands.map((entry) => ({
    ...entry,
    family: familyId(entry.command),
    effect: commandEffect(entry),
    confirmation: '',
    exitSemantics: exitSemantics(entry.command),
  })).map((entry) => ({ ...entry, confirmation: confirmation(entry, root) }));
}

function renderFamilyPage(meta, entries) {
  return `---
title: Pixel CLI - ${meta.name}
doc_type: reference
audience: [owner, operator, contributor, maintainer]
feature_status: ${meta.id === 'work' ? 'development-disabled' : 'mixed'}
owners: [documentation]
sources_of_truth: [pixel]
generated_by: scripts/docs/generate-cli-reference.mjs
last_verified_at: generated
---

${generatedNotice}

# ${meta.name}

**Status boundary:** ${meta.status}

| Command | Purpose | Effect | Confirmation | Exit semantics | Normative source |
|---|---|---|---|---|---|
${entries.map((entry) => `| \`./pixel ${entry.command}\` | ${markdownEscape(entry.description)} | ${markdownEscape(entry.effect)} | ${markdownEscape(entry.confirmation)} | ${markdownEscape(entry.exitSemantics)} | [\`${markdownEscape(entry.source)}\`](../../../${entry.source === 'pixel#usage' ? 'pixel' : entry.source}) |`).join('\n')}

Use the [owning guide](${meta.guide}) before a state-changing command. Return to the [CLI family index](README.md).
`;
}

function renderFamilyIndex(groups) {
  return `---
title: Pixel CLI command families
doc_type: reference
audience: [owner, operator, contributor, maintainer]
feature_status: mixed
owners: [documentation]
sources_of_truth: [pixel]
generated_by: scripts/docs/generate-cli-reference.mjs
last_verified_at: generated
---

${generatedNotice}

# Pixel CLI command families

The dispatcher exposes **${[...groups.values()].flat().length - 1} user-facing commands**, plus \`help\`. Each family page records purpose, state/mutation behavior, confirmation semantics, command-specific exit handling, and the normative dispatch source.

| Family | Commands | Status boundary |
|---|---:|---|
${families.map((meta) => `| [${meta.name}](${meta.slug}.md) | ${groups.get(meta.id)?.length ?? 0} | ${markdownEscape(meta.status)} |`).join('\n')}

Root help is a routing contract, not permission to run a privileged, destructive, provider, or external-effect command. Use preview/read-only modes and the owning guide first.
`;
}

export function renderCli(commands) {
  const groups = new Map(families.map((meta) => [meta.id, []]));
  for (const command of commands) groups.get(command.family).push(command);
  const sections = families.map((meta) => `## [${meta.name}](cli/${meta.slug}.md)\n\n${groups.get(meta.id).map((entry) => `- \`./pixel ${entry.command}\` — ${markdownEscape(entry.description)}`).join('\n')}`).join('\n\n');
  return `---
title: Pixel CLI reference
doc_type: reference
audience: [owner, operator, contributor, maintainer]
feature_status: mixed
owners: [documentation]
sources_of_truth: [pixel]
generated_by: scripts/docs/generate-cli-reference.mjs
last_verified_at: generated
---

${generatedNotice}

# Pixel CLI reference

The root dispatcher exposes **${commands.length - 1} user-facing commands**, plus \`help\`. This index is generated from the help and dispatch blocks together; generation fails if either side contains a stale or invented command.

Descriptions are concise routing summaries, not proof that a command is Supported or safe to run on a particular host. The [command-family index](cli/README.md) adds mutation, confirmation, exit, status, and source semantics. Review the linked guide and the command's own help or preview mode before any privileged, destructive, provider, or external-effect operation.

${sections}
`;
}

export function generateCliReference(mode, root = repoRoot) {
  const commands = enrich(extractCli(root), root);
  const baseline = readJson('scripts/docs/inventory-baseline.json', root);
  if (commands.length !== baseline.counts.rootCliEntriesIncludingHelp || commands.filter((entry) => entry.command !== 'help').length !== baseline.counts.rootCliCommandsExcludingHelp) {
    throw new Error(`CLI inventory changed: found ${commands.length - 1} commands plus help; review and update scripts/docs/inventory-baseline.json`);
  }
  writeGenerated('docs/reference/cli.md', renderCli(commands), mode, root);
  const groups = new Map(families.map((meta) => [meta.id, []]));
  for (const command of commands) groups.get(command.family).push(command);
  writeGenerated('docs/reference/cli/README.md', renderFamilyIndex(groups), mode, root);
  for (const meta of families) writeGenerated(`docs/reference/cli/${meta.slug}.md`, renderFamilyPage(meta, groups.get(meta.id)), mode, root);
  writeGenerated('docs/reference/cli.json', JSON.stringify({ schemaVersion: 1, generatedFrom: 'pixel', commands }, null, 2), mode, root);
  return commands;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) generateCliReference(modeFromArgs());
