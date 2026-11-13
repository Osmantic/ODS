import assert from 'node:assert/strict';

// Imported repository text selects from this fixed fixture ABI. It never
// supplies an identifier or expression interpolated into executable code.
const SDK_NAMES = new Set([
  'definePluginEntry', 'OPENCLAW_VERSION', 'abortAgentHarnessRun', 'abortAndDrainAgentHarnessRun',
  'callGatewayTool', 'resolveActiveEmbeddedRunSessionId', 'resolveUserPath', 'getSessionEntry',
  'patchSessionEntry', 'resolveStorePath', 'withSessionTranscriptWriteLock',
  'appendAssistantMirrorMessageByIdentity', 'extractBasicHtmlContent', 'extractAssistantVisibleText',
  'fetchWithWebToolsNetworkGuard', 'readResponseText', 'resolveAgentWorkspaceDir',
  'createOpenClawCodingTools', 'resolveSandboxContext',
]);

export function isolateSdkImports(source, hook) {
  assert.equal(typeof source, 'string');
  assert.equal(typeof hook, 'string');
  const stripped = source.replace(/import\s*\{([^}]+)\}\s*from\s*['"]openclaw\/plugin-sdk\/[^'"]+['"];?/g,
    (_match, names) => {
      for (const name of names.split(',').map(value => value.trim()).filter(Boolean)) {
        assert.ok(SDK_NAMES.has(name), 'Unknown SDK fixture binding; update the fixed ABI explicitly');
      }
      return '';
    });
  assert.doesNotMatch(stripped, /from\s*['"]openclaw\//, 'all SDK imports are isolated');
  return `const __odsFixtureSdkBridge = globalThis[${JSON.stringify(hook)}];
const definePluginEntry = entry => entry;
const OPENCLAW_VERSION = "2026.6.33";
const abortAgentHarnessRun = (...args) => __odsFixtureSdkBridge("abortAgentHarnessRun", args);
const abortAndDrainAgentHarnessRun = (...args) => __odsFixtureSdkBridge("abortAndDrainAgentHarnessRun", args);
const callGatewayTool = (...args) => __odsFixtureSdkBridge("callGatewayTool", args);
const resolveActiveEmbeddedRunSessionId = (...args) => __odsFixtureSdkBridge("resolveActiveEmbeddedRunSessionId", args);
const resolveUserPath = (...args) => __odsFixtureSdkBridge("resolveUserPath", args);
const getSessionEntry = (...args) => __odsFixtureSdkBridge("getSessionEntry", args);
const patchSessionEntry = (...args) => __odsFixtureSdkBridge("patchSessionEntry", args);
const resolveStorePath = (...args) => __odsFixtureSdkBridge("resolveStorePath", args);
const withSessionTranscriptWriteLock = (...args) => __odsFixtureSdkBridge("withSessionTranscriptWriteLock", args);
const appendAssistantMirrorMessageByIdentity = (...args) => __odsFixtureSdkBridge("appendAssistantMirrorMessageByIdentity", args);
const extractBasicHtmlContent = (...args) => __odsFixtureSdkBridge("extractBasicHtmlContent", args);
const extractAssistantVisibleText = (...args) => __odsFixtureSdkBridge("extractAssistantVisibleText", args);
const fetchWithWebToolsNetworkGuard = (...args) => __odsFixtureSdkBridge("fetchWithWebToolsNetworkGuard", args);
const readResponseText = (...args) => __odsFixtureSdkBridge("readResponseText", args);
const resolveAgentWorkspaceDir = (...args) => __odsFixtureSdkBridge("resolveAgentWorkspaceDir", args);
const createOpenClawCodingTools = (...args) => __odsFixtureSdkBridge("createOpenClawCodingTools", args);
const resolveSandboxContext = (...args) => __odsFixtureSdkBridge("resolveSandboxContext", args);
${stripped}`;
}
