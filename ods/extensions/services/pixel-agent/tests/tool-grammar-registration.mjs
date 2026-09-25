// Capture the real plugin registration without starting a gateway, reading an
// owner session, or invoking any tools. Only the OpenClaw SDK boundary is mocked;
// the plugin entry, factories, wrappers and their schemas execute unchanged.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

export async function registeredPixelTools({inspection = true} = {}) {
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
    pluginConfig: inspection ? {workspacePreviewInspectionTransport:'unix'} : {},
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
  const expected = manifest.contracts.tools.filter(name => inspection || name !== 'pixel_ods_workspace_preview_inspect');
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
