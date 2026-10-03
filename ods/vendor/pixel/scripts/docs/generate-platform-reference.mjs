import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  generatedNotice,
  markdownEscape,
  modeFromArgs,
  readJson,
  readText,
  repoRoot,
  writeGenerated,
} from './lib.mjs';

function groupForKey(key) {
  if (key.startsWith('PIXEL_SOURCE_') || key.startsWith('PIXEL_ACTION_')) return 'Source Broker';
  if (key.startsWith('PIXEL_OPS_')) return 'Operations Broker';
  if (key.startsWith('PIXEL_FRONTIER_')) return 'Frontier Broker';
  if (key.startsWith('PIXEL_WEB_COURIER_') || key.startsWith('PIXEL_COURIER_')) return 'Web Courier';
  if (key.startsWith('PIXEL_GOOGLE_')) return 'Google source custody';
  if (key.startsWith('PIXEL_REFERENCE_MODEL_')) return 'Reference model';
  return 'Gateway and owner deployment';
}

function boundaryForKey(key) {
  if (/(TOKEN|CREDENTIAL|POLICY|PRIVATE)/u.test(key)) return 'Private custody; do not copy or inspect contents for routine diagnosis';
  if (/(_UNIT|_TIMER)$/u.test(key)) return 'Service identity; verify the effective installed unit';
  if (/_PORT$/u.test(key)) return 'Configured listener input; bind and network policy decide exposure';
  return 'Generated/configured path; verify the installed host instead of assuming the example value';
}

export function buildPlatformReference(root = repoRoot) {
  const env = readText('.env.example', root);
  const locations = [];
  for (const match of env.matchAll(/^([A-Z][A-Z0-9_]*)=(?:'([^']*)'|"([^"]*)"|([^\n#]*))$/gmu)) {
    const key = match[1];
    if (!/(^OPENCLAW_HOME$|PIXEL_WORKSPACE$|PIXEL_INSTALL_DIR$|_PORT$|_DIR$|_PATH$|_UNIT$|_TIMER$)/u.test(key)) continue;
    locations.push({ key, value: match[2] ?? match[3] ?? match[4].trim(), group: groupForKey(key), boundary: boundaryForKey(key) });
  }
  if (locations.length < 10) throw new Error(`expected service/path inventory from .env.example; found ${locations.length}`);
  const qualification = readJson('QUALIFICATION-MATRIX.json', root);
  const manifest = readJson('RELEASE-MANIFEST.json', root);
  const compatibility = readJson('OPENCLAW-COMPATIBILITY.json', root);
  return { locations, qualification, manifest, compatibility };
}

function renderLocations(data) {
  const groups = new Map();
  for (const item of data.locations) {
    if (!groups.has(item.group)) groups.set(item.group, []);
    groups.get(item.group).push(item);
  }
  const sections = [...groups].map(([group, entries]) => `## ${group}\n\n| Configuration key | Repository example value | Evidence/privacy boundary |\n|---|---|---|\n${entries.map((entry) => `| \`${markdownEscape(entry.key)}\` | \`${markdownEscape(entry.value)}\` | ${markdownEscape(entry.boundary)} |`).join('\n')}`).join('\n\n');
  return `---
title: Services, paths, and ports
doc_type: reference
audience: [owner, operator, contributor, security-reviewer]
feature_status: mixed
owners: [documentation, operations, security]
sources_of_truth: [.env.example]
generated_by: scripts/docs/generate-platform-reference.mjs
last_verified_at: generated
---

${generatedNotice}

# Services, paths, and ports

These are sanitized repository example values, not proof of the paths, ports, units, enabled features, users, groups, or listeners on an installed host. Configuration can override them. Use \`./pixel verify\`, effective service properties, and the private generated environment to establish host truth without copying secret content.

${sections}

For safe health checks, use [verify and diagnose](../operations/verify-and-diagnose.md). For credential and process boundaries, use [the security boundary reference](../security/boundary-reference.md).
`;
}

function renderSupport(data) {
  const q = data.qualification;
  const deepWork = data.manifest.deepWorkCapability;
  const current = data.compatibility.combinations.filter((row) => row.pixel === data.manifest.pixel);
  if (current.length !== 1) throw new Error(`expected one current compatibility row; found ${current.length}`);
  return `---
title: Pixel support and qualification matrix
doc_type: reference
audience: [owner, operator, contributor, security-reviewer, maintainer]
feature_status: mixed
owners: [documentation, qualification, release]
sources_of_truth: [RELEASE-MANIFEST.json, QUALIFICATION-MATRIX.json, OPENCLAW-COMPATIBILITY.json]
generated_by: scripts/docs/generate-platform-reference.mjs
last_verified_at: generated
---

${generatedNotice}

# Pixel support and qualification matrix

This page reports repository contracts. It does not prove a fresh run on any host, and it does not broaden Pixel to other Linux distributions, Windows, macOS, hardware, providers, or models.

## Documented host lanes

| Lane | Host | Environment | Promotion-required | Provider calls | Credential inputs | Checks |
|---|---|---|---:|---:|---:|---|
${q.hostLanes.map((lane) => `| \`${markdownEscape(lane.id)}\` | ${markdownEscape(lane.host)} | ${markdownEscape(lane.environment)} | ${lane.requiredForPromotion ? 'yes' : 'no'} | ${lane.providerCallsAllowed ? 'allowed' : 'not allowed'} | ${lane.credentialInputsAllowed ? 'allowed' : 'not allowed'} | ${lane.checks.map((check) => `\`${markdownEscape(check)}\``).join(', ')} |`).join('\n')}

Automated/container lanes do not substitute for real system-service lanes. A declared lane does not claim that a fresh run exists for the current checkout.

## Capability profiles

| Profile | Email | Calendar | Social | Web | Operations | Frontier |
|---|---:|---:|---:|---:|---:|---:|
${q.capabilityProfiles.map((profile) => `| \`${markdownEscape(profile.id)}\` | ${profile.limbs.email ? 'on' : 'off'} | ${profile.limbs.calendar ? 'on' : 'off'} | ${profile.limbs.social ? 'on' : 'off'} | ${profile.limbs.web ? 'on' : 'off'} | ${profile.limbs.operations ? 'on' : 'off'} | ${profile.limbs.frontier ? 'on' : 'off'} |`).join('\n')}

Profiles select requested limbs; they do not supply credentials, policy, approval, target identity, provider qualification, or live evidence.

## Model-capacity guidance

| Memory band | Suggested local class | Context guidance | Fit guaranteed | Required validation |
|---|---|---|---:|---|
${q.modelCapacity.map((row) => `| \`${markdownEscape(row.memoryCapacityGiB)}\` | ${markdownEscape(row.localModelClass)} | ${markdownEscape(row.contextGuidance)} | ${row.fitIsGuaranteed ? 'yes' : 'no'} | ${row.requiredValidation.map((item) => `\`${markdownEscape(item)}\``).join(', ')} |`).join('\n')}

These are starting points, not a provider/model support promise. Measure the exact artifact, context, latency, memory, task quality, and owner acceptance.

## Current source-derived boundaries

- Compatibility row: **${markdownEscape(current[0].status)}**; see [generated status](../status.md) and [release evidence](../releases/evidence-index.md).
- Deep Work runtime enabled: \`${deepWork.runtimeEnabled}\`; admission boundary: \`${markdownEscape(deepWork.admissionBoundary)}\`.
- Publication requires the complete promotion gate set; qualification evidence does not grant publication or activation authority.
`;
}

export function generatePlatformReference(mode, root = repoRoot) {
  const data = buildPlatformReference(root);
  writeGenerated('docs/reference/services-paths-and-ports.md', renderLocations(data), mode, root);
  writeGenerated('docs/reference/support-matrix.md', renderSupport(data), mode, root);
  return { locations: data.locations.length, hostLanes: data.qualification.hostLanes.length };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) generatePlatformReference(modeFromArgs());
