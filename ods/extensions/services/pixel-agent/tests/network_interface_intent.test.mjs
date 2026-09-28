import test from 'node:test';
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';

const api = await import(process.env.GUARD_MODULE
  ? pathToFileURL(process.env.GUARD_MODULE).href : '../plugin/tool-loop-guard.mjs');

const ownerPrompt = 'Follow up on repo-workflows-20260928-1609/host-runtime.json. ' +
  'Fetch fresh ODS runtime status using the available ODS tools and record its evidence source and capture time. ' +
  'The 16/16 application-online projection is not the same as Docker health-check results and is not part of the host-observation job that inspected OS, CPU, memory, and services. ' +
  'Correct that distinction in the JSON and tell me exactly what container health this interface can and cannot establish. ' +
  'Preserve the verified host facts and their job ID. Stay read-only apart from updating this report; do not run arbitrary host commands or change services.';

for (const prompt of [
  ownerPrompt,
  'Tell me what this interface can establish about host container health.',
  'Explain what the API interface can establish about this host.',
  'What container health can this software interface establish for the ODS host?',
  'Tell me what this interface cannot establish about this host.',
]) {
  test(`software interface reference does not require network addresses: ${prompt}`, () => {
    assert.equal(api.userMessageOperationsRequirements([], prompt).actions.includes('host.network-addresses'), false);
  });
}

for (const prompt of [
  'Inspect this host. List interfaces and addresses.',
  'What interfaces does this computer have?',
  'Show me this host network interfaces and IP addresses.',
  'Inspect this host. Report interfaces only.',
  'Which interfaces are up on this machine?',
  'How many interfaces does this computer have?',
  'Tell me what this interface can establish. Also report the host IP addresses.',
  'Tell me what this interface can establish and show the network interfaces on this host.',
  'Report this host network interfaces and tell me what this interface can establish.',
]) {
  test(`explicit interface request retains required observations: ${prompt}`, () => {
    const result = api.userMessageOperationsRequirements([], prompt);
    assert.equal(result.required, true);
    assert.equal(result.actions.includes('host.network-addresses'), true);
  });
}

test('latest owner request does not inherit a prior interface request', () => {
  const result = api.userMessageOperationsRequirements([
    {role: 'user', content: 'Show the network interfaces on this host.'},
    {role: 'assistant', content: 'The network inspection is complete.'},
    {role: 'user', content: ownerPrompt},
  ]);
  assert.equal(result.actions.includes('host.network-addresses'), false);
});

test('broad host exploration still requires addresses and respects exclusions', () => {
  const prompt = 'Explore the ODS host machine you are running on and tell me what is here.';
  assert.equal(api.userMessageOperationsRequirements([], prompt).actions.includes('host.network-addresses'), true);
  const excluded = api.userMessageOperationsRequirements([], `${prompt} Do not inspect interfaces or IP addresses.`);
  assert.equal(excluded.actions.includes('host.network-addresses'), false);
});
