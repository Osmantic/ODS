import {createHash} from 'node:crypto';
import path from 'node:path';

// Reviewed vendor defaults only. Unknown revisions and any owner edits retain
// their complete original text. No workspace file is read or written here.
const DEFAULT_DIGESTS = Object.freeze({
  'AGENTS.md': 'c5fe405d7c9a243ef21660aea711de14164dd1b245613f0970026f1881f4a225',
  'TOOLS.md': '1c575473578d8db98838c1587ce27e0be6ee6d51e2dab2539304f68f7dd8ea53',
});
export const CALENDAR_TOOLS = Object.freeze(['pixel_calendar_list', 'pixel_calendar_get',
  'pixel_calendar_propose_create', 'pixel_calendar_propose_update', 'pixel_calendar_propose_delete']);
export const FRONTIER_TOOLS = Object.freeze(['pixel_frontier_plan_review', 'pixel_frontier_failure_triage',
  'pixel_frontier_job_get', 'pixel_frontier_job_wait', 'pixel_frontier_job_events',
  'pixel_frontier_job_cancel', 'pixel_frontier_usage', 'pixel_frontier_finalize']);
const CALENDAR_UNAVAILABLE = 'Calendar tools are disabled for this agent. Do not claim to read or change events or substitute another tool for Calendar access.\n\n';
const FRONTIER_UNAVAILABLE = 'Frontier tools are disabled for this agent. Do not route Frontier requests through web, shell, Operations, or another limb.\n\n';
const PRODUCT_EXECUTION = '## Local execution\n\nUse the model and tools configured for this ODS installation. Do not assume access to another owner\'s machines, models, or accounting services. Verify tool results before claiming completion.\n\n';

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

function reviewedSection(text, digest, replacement) {
  const boundaries = [...text.matchAll(/^## /gm)].map(match => match.index);
  boundaries.push(text.length);
  for (let index = 0; index < boundaries.length - 1; index++) {
    const start = boundaries[index], end = boundaries[index + 1];
    if (createHash('sha256').update(text.slice(start, end)).digest('hex') === digest)
      return text.slice(0, start) + replacement + text.slice(end);
  }
  throw new Error('reviewed-bootstrap-section-missing');
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
    const file = files[index], expected = DEFAULT_DIGESTS[file?.name];
    if (!expected || file.missing || typeof file.content !== 'string'
        || file.path !== path.join(path.resolve(workspace), file.name)
        || createHash('sha256').update(file.content).digest('hex') !== expected) continue;
    let content = file.content;
    if (file.name === 'AGENTS.md') {
      content = reviewedSection(content, 'f4ae5ee982981b26ec0b4130772d4ed6e7519eec0ae77650a777325bfa79b818', PRODUCT_EXECUTION);
      if (frontierDisabled) content = section(content, 'Frontier work follows a narrower boundary.', '## Calendar: bounded direct actions and approval\n', FRONTIER_UNAVAILABLE);
      if (calendarDisabled) content = section(content, '## Calendar: bounded direct actions and approval\n', '## Perception limits\n', CALENDAR_UNAVAILABLE);
    } else if (frontierDisabled) {
      content = section(content, '## Frontier limb\n', null, FRONTIER_UNAVAILABLE);
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
