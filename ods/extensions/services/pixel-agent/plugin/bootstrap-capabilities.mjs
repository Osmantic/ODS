import {createHash} from 'node:crypto';
import path from 'node:path';

// Capability trimming applies to reviewed vendor defaults only. Unknown revisions
// and owner edits keep their complete text, apart from exact retired blocks below.
// No workspace file is read or written here.
const DEFAULT_DIGESTS = Object.freeze({
  'AGENTS.md': '54691ec83d97a2edc9ea20b546228ffb32240f7688b662ad3da756864ff8ddb1',
  'TOOLS.md': '1c575473578d8db98838c1587ce27e0be6ee6d51e2dab2539304f68f7dd8ea53',
});
// Retired template text that earlier ODS Pixel bundles copied into workspaces (#6156).
// The upgrade migration (vendor/pixel/scripts/migrate-retired-workspace-text.mjs) removes
// it from disk. Where that step did not run or could not change a file, an exact copy
// is also left out of the prompt here. Same SHA-256 table as that migration: canonical
// LF text, never the retired wording. `headings` only lets the migration report an
// edited copy for review; an edited copy stays in the prompt.
export const RETIRED_TEXT = Object.freeze({
  'AGENTS.md': Object.freeze({sections: Object.freeze(['a79b56d5d9f5d76a1bb643bc53d37b97104ccc628182ec013edde8ed865b683f']),
    headings: Object.freeze(['17cb8a4a9627a915ba26fc2eb152d64d297bfba35f014e1a0770c96fc7f3cce9']),
    lines: Object.freeze([]), emptyHeadings: Object.freeze([])}),
  'MEMORY.md': Object.freeze({sections: Object.freeze([]), headings: Object.freeze([]),
    lines: Object.freeze(['73195176060dcedc9a4ac7f0abf1590f3c275a9c743aa7bf562303207819ccf3']),
    emptyHeadings: Object.freeze(['## Standing operating decisions'])}),
});
export const CALENDAR_TOOLS = Object.freeze(['pixel_calendar_list', 'pixel_calendar_get',
  'pixel_calendar_propose_create', 'pixel_calendar_propose_update', 'pixel_calendar_propose_delete']);
export const FRONTIER_TOOLS = Object.freeze(['pixel_frontier_plan_review', 'pixel_frontier_failure_triage',
  'pixel_frontier_job_get', 'pixel_frontier_job_wait', 'pixel_frontier_job_events',
  'pixel_frontier_job_cancel', 'pixel_frontier_usage', 'pixel_frontier_finalize']);
const CALENDAR_UNAVAILABLE = 'Calendar tools are disabled for this agent. Do not claim to read or change events or substitute another tool for Calendar access.\n\n';
const FRONTIER_UNAVAILABLE = 'Frontier tools are disabled for this agent. Do not route Frontier requests through web, shell, Operations, or another limb.\n\n';
const BOM = '﻿';

function denied(name, patterns) {
  return Array.isArray(patterns) && patterns.some(pattern => typeof pattern === 'string'
    && (pattern === name || pattern === '*' || (pattern.endsWith('*')
      && !pattern.slice(0, -1).includes('*') && name.startsWith(pattern.slice(0, -1)))));
}

function provenDisabled(cfg, agent, tools, pluginId) {
  if (cfg.plugins?.enabled === false || cfg.plugins?.entries?.[pluginId]?.enabled === false) return true;
  // An absent initial schema is not a disabled capability: Tool Search can
  // expose it later. Only explicit plugin disablement or complete denials count.
  return tools.every(name => denied(name, agent.tools?.deny) || denied(name, cfg.tools?.deny));
}

function section(text, start, end, replacement = '') {
  const a = text.indexOf(start), b = end ? text.indexOf(end, a + start.length) : text.length;
  if (a < 0 || b < a) throw new Error('reviewed-bootstrap-boundary-missing');
  return text.slice(0, a) + replacement + text.slice(b);
}

const sha256 = value => createHash('sha256').update(value).digest('hex');

function textLines(text) {
  const lines = [];
  for (let start = text.startsWith(BOM) ? 1 : 0; start < text.length;) {
    const newline = text.indexOf('\n', start), next = newline < 0 ? text.length : newline + 1;
    let end = newline < 0 ? text.length : newline;
    if (end > start && text[end - 1] === '\r') end--;
    lines.push({start, next, text: text.slice(start, end)});
    start = next;
  }
  return lines;
}

// Mirrors removeRetiredWorkspaceText in the vendored migration: exact blocks only,
// with CRLF and trailing empty lines normalized for matching; all other text is kept.
export function removeRetiredText(name, text, table = RETIRED_TEXT) {
  const retired = Object.hasOwn(table, name) ? table[name] : null;
  if (!retired) return text;
  const lines = textLines(text), drop = new Array(lines.length).fill(false);
  const heading = index => /^##? /.test(lines[index].text), blank = index => /^[ \t]*$/.test(lines[index].text);
  const nextHeading = after => {
    for (let index = after + 1; index < lines.length; index++) if (heading(index)) return index;
    return lines.length;
  };
  let removed = 0;
  for (let index = 0; retired.sections.length && index < lines.length; index++) {
    if (!heading(index)) continue;
    const end = nextHeading(index);
    let last = end;
    while (last > index + 1 && lines[last - 1].text === '') last--;
    const canonical = lines.slice(index, last).map(line => `${line.text}\n`).join('');
    if (retired.sections.includes(sha256(canonical))) { drop.fill(true, index, end); removed++; }
    index = end - 1;
  }
  const removedLines = [];
  for (let index = 0; retired.lines.length && index < lines.length; index++) {
    if (!drop[index] && retired.lines.includes(sha256(lines[index].text))) { drop[index] = true; removedLines.push(index); removed++; }
  }
  for (const index of removedLines) {
    let owner = index - 1;
    while (owner >= 0 && !heading(owner)) owner--;
    if (owner < 0 || drop[owner] || !retired.emptyHeadings.includes(lines[owner].text)) continue;
    const end = nextHeading(owner);
    let empty = true;
    for (let line = owner + 1; line < end; line++) if (!drop[line] && !blank(line)) empty = false;
    if (empty) drop.fill(true, owner, end);
  }
  if (!removed) return text;
  // A block removed from the end of the file must not leave trailing blank lines behind.
  if (drop[lines.length - 1]) {
    for (let index = lines.length - 1; index >= 0; index--) {
      if (drop[index]) continue;
      if (!blank(index)) break;
      drop[index] = true;
    }
  }
  return (text.startsWith(BOM) ? BOM : '')
    + lines.filter((_, index) => !drop[index]).map(line => text.slice(line.start, line.next)).join('');
}

export function filterBootstrapCapabilities(event) {
  const context = event?.context, cfg = context?.cfg;
  if (event?.type !== 'agent' || event.action !== 'bootstrap' || context?.agentId !== 'pixel'
      || !cfg || cfg.hooks?.internal?.enabled === false
      || cfg.plugins?.enabled === false || cfg.plugins?.entries?.['pixel-ods']?.enabled !== true
      || cfg.plugins.entries['pixel-ods'].hooks?.allowPromptInjection === false) return false;
  const agents = Array.isArray(cfg.agents?.list) ? cfg.agents.list.filter(agent => agent?.id === 'pixel') : [];
  if (agents?.length !== 1) return false;
  const agent = agents[0], workspace = agent.workspace ?? cfg.agents?.defaults?.workspace;
  if (typeof workspace !== 'string' || !path.isAbsolute(workspace)
      || typeof context.workspaceDir !== 'string' || !path.isAbsolute(context.workspaceDir)
      || path.resolve(workspace) !== path.resolve(context.workspaceDir)
      || !Array.isArray(context.bootstrapFiles)) return false;
  const calendarDisabled = provenDisabled(cfg, agent, CALENDAR_TOOLS, 'pixel-source-broker');
  const frontierDisabled = provenDisabled(cfg, agent, FRONTIER_TOOLS, 'pixel-frontier-broker');
  const files = context.bootstrapFiles;
  // Build all changes before publishing; do not mutate original file objects.
  const updates = [];
  for (let index = 0; index < files.length; index++) {
    const file = files[index], name = file?.name, expected = DEFAULT_DIGESTS[name];
    if ((!expected && !Object.hasOwn(RETIRED_TEXT, name ?? '')) || file.missing || typeof file.content !== 'string'
        || file.path !== path.join(path.resolve(workspace), name)) continue;
    // Exact retired blocks leave the prompt even from owner-edited files; nothing else does.
    let content = removeRetiredText(name, file.content);
    if (expected && sha256(content) === expected) {
      if (name === 'AGENTS.md') {
        if (frontierDisabled) content = section(content, 'Frontier work follows a narrower boundary.', '## Calendar: bounded direct actions and approval\n', FRONTIER_UNAVAILABLE);
        if (calendarDisabled) content = section(content, '## Calendar: bounded direct actions and approval\n', '## Perception limits\n', CALENDAR_UNAVAILABLE);
      } else if (frontierDisabled) {
        content = section(content, '## Frontier limb\n', null, FRONTIER_UNAVAILABLE);
      }
    }
    if (content !== file.content) updates.push([index, {...file, content}]);
  }
  // The SDK copies the hook context object, but shares this array. Replacing
  // entries is its supported override boundary; assigning a new array is lost.
  for (const [index, file] of updates) files[index] = file;
  return updates.length > 0;
}

export function registerBootstrapCapabilities(api) {
  api.registerHook?.('agent:bootstrap', event => filterBootstrapCapabilities(event), {
    name: 'pixel-ods-capability-bootstrap',
    description: 'Scope exact shipped bootstrap defaults to the configured Pixel capabilities without changing owner files.',
  });
}
