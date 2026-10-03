import test from 'node:test';
import assert from 'node:assert/strict';
import { createToolLoopGuard, userMessageNetworkPeerRequest, userMessageOperationsRequirements } from '../plugin/tool-loop-guard.mjs';

const check = (text) => userMessageNetworkPeerRequest([], text);
const defaults = [22, 80, 443, 3389, 5985, 5986];

test('explicit endpoint aliases bind the stated private address and requested ports', () => {
  for (const text of [
    'Check a specific LAN target: physical Tower2 at 192.168.0.175, TCP port 22.',
    'Check LAN target 192.168.0.175 port 22.',
    'Check reachability of printer.local at 10.42.0.5 on the LAN port 22.',
    'Probe printer.local at 10.42.0.5 using SSH.',
  ]) {
    const expected = text.includes('192.168.') ? '192.168.0.175' : '10.42.0.5';
    assert.deepEqual(check(text), { peer: expected, ports: [22] }, text);
  }
  assert.deepEqual(check('Probe nas.local on the network port 445.'), { peer: 'nas.local', ports: [445] });
  assert.deepEqual(check('Resolve "nas.local." on the network.'), { peer: 'nas.local.', ports: defaults });
});

test('alias binding preserves owner exclusions and refuses ambiguous endpoints', () => {
  for (const text of [
    'Probe alpha at 192.168.2.8 using SSH. Do not contact alpha.',
    'Probe alpha at 192.168.2.8 on the LAN. Do not contact 192.168.2.8.',
    'Probe alpha at 8.8.8.8 on the LAN.',
    'Probe alpha at 192.168.2.999 on the LAN.',
    'Probe alpha.local and beta.local on the LAN.',
    'On the LAN. Probe alpha.local and beta.local.',
    'Probe alpha.local, beta.local, gamma.local on the LAN.',
    'Probe alpha.local on the LAN. Probe beta.local on the LAN.',
    'Check LAN target 192.168.2.8. Check LAN target 192.168.2.9.',
    'Probe 192.168.2.0/24 on the LAN.',
    'Inspect https://alpha.local on the network.',
    'The initial Node probe used an old image. Check the files.',
    'Resolve its path and check the module.',
  ]) assert.equal(check(text), undefined, text);
  assert.deepEqual(check('Probe alpha.local on the LAN. Do not contact beta.local.'), { peer: 'alpha.local', ports: defaults });
  assert.deepEqual(check('Probe alpha.local on the LAN. Do not contact the host named beta.local.'), { peer: 'alpha.local', ports: defaults });
});

test('remote observations do not require background local facts', () => {
  const text = 'Check a specific LAN target: physical Tower2 at 192.168.0.175. ' +
    'I obtained that address from a fresh host route lookup. ' +
    'Use installed ODS network-peer tools for a read-only check of TCP port 22; ' +
    'if an already-authorized SSH tool can verify remote hostname, use it. ' +
    'Keep reachability, an SSH banner and authenticated remote identity distinct. ' +
    'Save remote-check.md with actual receipts, then read it back. ' +
    'Do not substitute local guest facts for remote identity.';
  assert.deepEqual(userMessageOperationsRequirements([], text), {
    required: true, actions: ['host.network-peer'], networkPeer: { peer: '192.168.0.175', ports: [22] },
  });
  for (const local of ['Check the local host CPU and memory.', 'Inspect this laptop. Report CPU and RAM.']) {
    const result = userMessageOperationsRequirements([], `${local} Separately check reachability of nas at 10.2.3.4 on the LAN port 445.`);
    assert.equal(result.required, true);
    assert.deepEqual(result.actions, ['host.cpu', 'host.memory', 'host.network-peer']);
  }
  assert.deepEqual(userMessageOperationsRequirements([], 'Check this host network addresses, routes and listening ports. Separately check reachability of printer at 10.2.3.4 on the LAN port 9100.').actions,
    ['host.network-addresses', 'host.network-routes', 'host.listening-ports', 'host.network-peer']);
});

test('runtime admission accepts the bound address and rejects another target', () => {
  const guard = createToolLoopGuard();
  const ctx = { agentId: 'pixel', runId: 'peer-scope', sessionId: 'peer-session' };
  guard.observeRun(ctx, 'pixel', { prompt: 'Check LAN target: printer at 10.2.3.4 port 9100.' });
  const invoke = (peer) => guard.beforeToolCall({ toolName: 'tool_call', params: {
    id: 'pixel_ods_host_observe', args: { actions: ['host.network-peer'], peer, ports: [9100] },
  } }, ctx);
  assert.equal(invoke('10.2.3.5')?.block, true);
  assert.deepEqual(invoke('10.2.3.4')?.params?.args, { actions: ['host.network-peer'], peer: '10.2.3.4', ports: [9100] });
});
