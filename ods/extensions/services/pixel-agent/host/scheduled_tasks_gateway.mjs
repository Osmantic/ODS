// Fixed owner-side SDK bridge, launched only after ODS binds the managed owner
// and gateway. Credentials cross stdin, never command arguments or stdout.
import {pathToFileURL} from 'node:url';

const METHODS = new Set(['cron.list', 'cron.get', 'cron.add', 'cron.update', 'cron.remove', 'cron.runs']);
const MAX_INPUT = 32768;
let raw = '';
for await (const chunk of process.stdin) {
  raw += chunk;
  if (Buffer.byteLength(raw) > MAX_INPUT) throw new Error('invalid-scheduler-request');
}
const input = JSON.parse(raw);
if (!input || Object.keys(input).sort().join(',') !== 'method,packageDir,params,port,token'
    || !METHODS.has(input.method) || !Number.isInteger(input.port) || input.port < 1 || input.port > 65535
    || typeof input.token !== 'string' || !/^[!-~]{16,4096}$/.test(input.token)
    || typeof input.packageDir !== 'string' || !input.packageDir.startsWith('/') || input.packageDir.includes('\0')
    || !input.params || typeof input.params !== 'object' || Array.isArray(input.params)) {
  throw new Error('invalid-scheduler-request');
}
try {
  const {callGatewayFromCli} = await import(pathToFileURL(`${input.packageDir}/dist/plugin-sdk/gateway-runtime.js`));
  const read = ['cron.list', 'cron.get', 'cron.runs'].includes(input.method);
  const result = await callGatewayFromCli(input.method, {
    url: `ws://127.0.0.1:${input.port}`, token: input.token, timeout: '10000',
  }, input.params, {clientName: 'gateway-client', mode: 'backend', deviceIdentity: null,
    scopes: [read ? 'operator.read' : 'operator.admin']});
  const encoded = JSON.stringify({ok: true, result});
  if (Buffer.byteLength(encoded) > 1024 * 1024) throw new Error('scheduler-response-too-large');
  process.stdout.write(`${encoded}\n`);
} catch (error) {
  // A missing response never proves that a mutating RPC did not take effect.
  // The caller preserves its pending creation receipt and reconciles, not replays.
  const missing = input.method === 'cron.get' && typeof input.params.id === 'string'
    && error?.name === 'GatewayClientRequestError' && error.message === `cron job not found: ${input.params.id}`;
  process.stdout.write(`${JSON.stringify({ok: false, code: missing ? 'task-not-found' : 'scheduler-unavailable'})}\n`);
  process.exitCode = 1;
}
