// Capture the real plugin registration without starting a gateway, reading an
// owner session, or invoking any tools. Only the OpenClaw SDK boundary is mocked;
// the plugin entry, factories, wrappers and their schemas execute unchanged.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {registerHooks} from 'node:module';
import {join} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

// Use the shipped Source factories and locked TypeBox, not copies of their
// schemas. Registration creates tool objects only; no executor is invoked.
export async function registeredSourceReadTools() {
  const pkg = process.env.OPENCLAW_PACKAGE_DIR;
  const typebox = process.env.PIXEL_TEST_TYPEBOX_DIR;
  assert.ok(pkg && typebox, 'actual pinned SDK and locked Source TypeBox are required');
  const source = new URL('../../../../vendor/pixel/plugin/', import.meta.url);
  assert.equal(JSON.parse(await readFile(join(typebox, 'package.json'))).version,
    JSON.parse(await readFile(new URL('package.json', source))).dependencies.typebox);
  const aliases = {
    typebox: pathToFileURL(join(typebox, 'build/index.mjs')).href,
    'typebox/value': pathToFileURL(join(typebox, 'build/value/index.mjs')).href,
    'openclaw/plugin-sdk/plugin-entry': pathToFileURL(join(pkg, 'dist/plugin-sdk/plugin-entry.js')).href,
  };
  const loader = registerHooks({resolve(specifier, context, next) {
    return context.parentURL?.startsWith(source.href) && aliases[specifier]
      ? {url: aliases[specifier], shortCircuit: true} : next(specifier, context);
  }});
  const flags = {PIXEL_AGENT_ID: 'pixel', PIXEL_LIMB_EMAIL_ENABLED: '1',
    PIXEL_LIMB_CALENDAR_ENABLED: '1', PIXEL_LIMB_CALENDAR_WRITE_ENABLED: '0',
    PIXEL_CALENDAR_DIRECT_ENABLED: '0', PIXEL_LIMB_EMAIL_DRAFTS_ENABLED: '0',
    PIXEL_LIMB_WEB_ENABLED: '0', PIXEL_LIMB_SOCIAL_ENABLED: '0',
    PIXEL_LIMB_OPERATIONS_ENABLED: '0', PIXEL_LIMB_FRONTIER_ENABLED: '0'};
  const saved = Object.fromEntries(Object.keys(flags).map(key => [key, process.env[key]]));
  const tools = [];
  try {
    Object.assign(process.env, flags);
    const {default: entry} = await import(pathToFileURL(join(fileURLToPath(source), 'index.js')));
    entry.register({config: {agents: {defaults: {userTimezone: 'America/Sao_Paulo'}}},
      registerTool(factory, {names}) {
        const tool = factory({agentId: 'pixel'});
        assert.ok(tool && typeof tool.execute === 'function');
        assert.deepEqual(names, [tool.name]);
        tools.push(tool);
      }});
  } finally {
    loader.deregister();
    for (const [key, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
  }
  assert.deepEqual(tools.map(tool => tool.name).sort(), ['pixel_limb_status',
    'pixel_gmail_inbox', 'pixel_gmail_sent', 'pixel_gmail_search', 'pixel_gmail_read',
    'pixel_gmail_thread', 'pixel_gmail_count', 'pixel_calendar_list', 'pixel_calendar_get',
    'pixel_calendar_count'].sort(), 'read-only fixture must not register an action tool');
  return tools.filter(tool => tool.name !== 'pixel_limb_status');
}

export async function registeredPixelTools({inspection = true, project = true} = {}) {
  const entry = new URL('../plugin/index.js', import.meta.url);
  const source = await readFile(entry, 'utf8');
  const isolated = source.replace(/from\s+(['"])([^'"]+)\1/g, (match, quote, specifier) => {
    if (specifier.startsWith('.')) return `from ${JSON.stringify(new URL(specifier, entry).href)}`;
    if (specifier === 'node:url') return match;
    if (!specifier.startsWith('openclaw/plugin-sdk/')) throw new Error(`Unexpected plugin import: ${specifier}`);
    return match;
  });
  // Import declarations retain the actual names. Unexpected SDK calls fail
  // closed, rather than accidentally invoking a host or swallowing a change.
  const stubs = isolated.replace(/import\s*\{([^}]+)\}\s*from\s*['"]openclaw\/plugin-sdk\/[^'"]+['"];?/g,
    (_match, names) => names.split(',').map(raw => {
      const name = raw.trim();
      if (!name) return '';
      assert.match(name, /^[A-Za-z_$][\w$]*$/);
      if (name === 'definePluginEntry') return 'const definePluginEntry = entry => entry;';
      if (name === 'OPENCLAW_VERSION') return 'const OPENCLAW_VERSION = "2026.6.33";';
      return `const ${name} = () => { throw new Error("SDK execution during schema discovery: ${name}"); };`;
    }).join('\n'));
  assert.doesNotMatch(stubs, /from\s*['"]openclaw\//, 'all SDK imports must be isolated');
  const {default: plugin} = await import(`data:text/javascript;base64,${Buffer.from(`${stubs}\n//# sourceURL=${entry.href}?schema-discovery`).toString('base64')}`);
  const tools = [];
  const context = {agentId: 'pixel', sessionKey: `agent:pixel:openai-user:ods-${'a'.repeat(64)}`};
  plugin.register({
    registrationMode: 'discovery',
    config: {agents: {list: [{id: 'pixel', sandbox: {mode: 'off'}, tools: {exec: {host: 'gateway'}}}]}},
    pluginConfig: {...(inspection ? {workspacePreviewInspectionTransport:'unix'} : {}),
      ...(project ? {projectBuildSocket:'/var/lib/ods-pixel-project/control.sock'} : {})},
    logger: {warn() {}}, on() {}, registerHttpRoute() {},
    registerTool(factory, options) {
      const tool = typeof factory === 'function' ? factory(context) : factory;
      assert.ok(tool, `registration returned no tool: ${options?.names}`);
      assert.deepEqual(options.names, [tool.name]);
      assert.ok(tool.parameters && typeof tool.parameters === 'object', tool.name);
      tools.push(tool);
    },
  });
  const manifest = JSON.parse(await readFile(new URL('../plugin/openclaw.plugin.json', import.meta.url)));
  const expected = manifest.contracts.tools.filter(name =>
    (inspection || name !== 'pixel_ods_workspace_preview_inspect')
    && (project || name !== 'pixel_ods_project_build'));
  assert.deepEqual(tools.map(tool => tool.name).sort(), expected.sort(),
    'compile every registered tool, including future registrations');
  assert.equal(new Set(tools.map(tool => tool.name)).size, tools.length);
  return tools;
}

// A union of the real schemas catches failures caused by any offered tool, even
// when the requested task would never call it. This is schema-converter coverage,
// not a substitute for backend/chat-template or installed user-journey testing.
export function combinedToolSchema(tools) {
  assert.ok(tools.length > 0);
  assert.equal(new Set(tools.map(tool => tool.name)).size, tools.length);
  return {oneOf: tools.map(tool => ({
    type: 'object', additionalProperties: false, required: ['name', 'arguments'],
    properties: {name: {const: tool.name}, arguments: tool.parameters},
  }))};
}
