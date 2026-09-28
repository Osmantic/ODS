import test from 'node:test';
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';

const api = await import(process.env.GUARD_MODULE
  ? pathToFileURL(process.env.GUARD_MODULE).href : '../plugin/tool-loop-guard.mjs');
const url = 'https://nodejs.org/dist/v22.23.2/node-v22.23.2-linux-x64.tar.xz';
const installPrompt = `Use the existing Operations artifact staging and owner-workspace promotion tools to acquire ${url}. ` +
  'Keep the archive and extracted toolchain under repo-workflows/toolchains; ' +
  'do not install system packages or change host, service, sandbox security, or global user settings.';

for (const prompt of [
  installPrompt,
  'Stage the archive via Operations and promote it into the workspace; do not change host or service configuration.',
  'Use Operations to install the toolchain; do not modify host services, but tell me the hostname.',
  'Do not change host services. Use Operations to stage the artifact.',
]) {
  test(`a host-service change prohibition does not request service observation: ${prompt}`, () => {
    assert.equal(api.userMessageOperationsRequirements([], prompt).actions.includes('host.services'), false);
  });
}

for (const prompt of [
  'Use Operations to check the host services and report which are running.',
  "Inspect this machine's systemd services and report their status.",
  'Give me a broad inventory of this host: services, processes, and uptime.',
  'Check the health of this machine and report service status.',
  'Do not change host services. Report this host service status.',
]) {
  test(`positive service observation remains required: ${prompt}`, () => {
    const requirements = api.userMessageOperationsRequirements([], prompt);
    assert.equal(requirements.required, true);
    assert.equal(requirements.actions.includes('host.services'), true);
  });
}

function stageDecision(prompt, wrapped) {
  const guard = api.createToolLoopGuard();
  const ctx = {agentId: 'pixel', runId: 'portable-node', sessionId: 'node-session'};
  guard.observeRun(ctx, 'pixel', {prompt});
  const args = {url, filename: 'node-v22.23.2-linux-x64.tar.xz'};
  if (wrapped) {
    const outer = guard.beforeToolCall({toolName: 'tool_call', params: {
      id: 'openclaw:pixel-operations-broker:pixel_ops_download_stage', args,
    }}, {...ctx, toolName: 'tool_call'}, 'pixel');
    if (outer?.block) return outer;
  }
  return guard.beforeToolCall({toolName: 'pixel_ops_download_stage', params: args},
    {...ctx, toolName: 'pixel_ops_download_stage'}, 'pixel');
}

for (const wrapped of [false, true]) {
  test(`portable artifact stage composes with a no-service-change constraint: ${wrapped}`, () => {
    const decision = stageDecision(installPrompt, wrapped);
    assert.notEqual(decision?.block, true, decision?.blockReason);
  });
  test(`a genuine requested service inspection still binds submissions: ${wrapped}`, () => {
    assert.equal(stageDecision('Check the ODS host services.', wrapped)?.block, true);
  });
}
