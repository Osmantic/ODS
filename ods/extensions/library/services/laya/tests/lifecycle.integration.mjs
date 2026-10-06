// Exercise the real managed connection and tool against an already-ready service.
import assert from 'node:assert/strict';
import {readFileSync, renameSync} from 'node:fs';
import {join} from 'node:path';
import {configureLayaPortal} from '../../../../services/pixel-agent/plugin/laya-setup.mjs';
import {createLayaRuntime, readLayaConnection, LAYA_CONNECTION_FILE} from '../../../../services/pixel-agent/plugin/laya-runtime.mjs';

const root = process.env.LAYA_TEST_INSTALL_ROOT;
assert.ok(root, 'A dedicated test installation is required');
const port = Number(process.env.LAYA_TEST_PORT ?? 8017);
const marker = join(root, 'data/user-extensions/laya/compose.yaml');
const disabled = `${marker}.disabled`;
const original = readFileSync(LAYA_CONNECTION_FILE);
configureLayaPortal({installRoot: root, port});
assert.deepEqual(readFileSync(LAYA_CONNECTION_FILE), original, 'setup preserves credentials and binding');
assert.ok(readLayaConnection());
const runtime = createLayaRuntime();
const cachedTool = runtime.offered();
assert.ok(cachedTool);
const args = {items: [{id: 'ticket', text: 'I was charged twice. I need a refund.'}],
  questions: [{id: 'refund', type: 'noul', instructions: 'Does this customer request a refund?'}],
  checkpoint: 'english'};
let reply = await cachedTool.execute('enabled', args);
assert.equal(reply.isError, undefined);
assert.equal(JSON.parse(reply.content[0].text).completeContext, true);
renameSync(marker, disabled);
try {
  assert.equal(runtime.offered(), null);
  reply = await cachedTool.execute('disabled', args);
  assert.equal(reply.details.status, 'disabled');
  assert.equal(reply.details.inferenceSubmitted, false);
} finally { renameSync(disabled, marker); }
configureLayaPortal({installRoot: root, port});
reply = await cachedTool.execute('reenabled', args);
assert.equal(reply.isError, undefined);
assert.equal(reply.details.executionAuthorized, false);
assert.deepEqual(readFileSync(LAYA_CONNECTION_FILE), original);
console.log('Managed Laya setup, tool invocation, disable and re-enable passed.');
