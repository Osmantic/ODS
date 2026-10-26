// Owner-side adapter to the MCP OAuth implementation in ODS's pinned OpenClaw.
// The custodian must select the existing native server and bind the owner/state
// directory before launch. This is not a public endpoint or an enrollment API.
// Credentials and callback codes cross stdin, never argv or public diagnostics.
import {createHash} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

const VERSION = '2026.6.33';
// No public MCP OAuth SDK export exists at this pin. Refuse different bytes
// until the adapter is reviewed, rather than guessing another compiled chunk.
const MODULE = 'dist/agent-bundle-mcp-runtime-tW2Zz-uG.js';
const SHA256 = 'ed2e1d92189326625e7c8a735b83ede3942e1597e547587d8ee6413a7fd23bf0';
const OPERATIONS = new Set(['status', 'begin', 'complete', 'forget']);
const MAX_INPUT = 32768;

class Refusal extends Error {}

async function readRequest() {
  const chunks = [];
  let size = 0;
  for await (const chunk of process.stdin) {
    size += chunk.length;
    if (size > MAX_INPUT) throw new Refusal('invalid-native-oauth-request');
    chunks.push(chunk);
  }
  let input;
  try { input = JSON.parse(Buffer.concat(chunks).toString('utf8')); }
  catch (error) {
    if (!(error instanceof SyntaxError)) throw error;
    throw new Refusal('invalid-native-oauth-request');
  }
  const expected = input?.operation === 'complete'
    ? 'authorizationCode,operation,packageDir,server,serverName' : 'operation,packageDir,server,serverName';
  if (!input || typeof input !== 'object' || Object.keys(input).sort().join(',') !== expected
      || !OPERATIONS.has(input.operation) || typeof input.packageDir !== 'string'
      || !path.isAbsolute(input.packageDir) || input.packageDir.includes('\0')
      || typeof input.serverName !== 'string' || !/^[a-z][a-z0-9-]{1,29}$/.test(input.serverName)
      || !input.server || typeof input.server !== 'object' || Array.isArray(input.server)
      || input.server.auth !== 'oauth'
      || (input.operation === 'complete' && (typeof input.authorizationCode !== 'string'
        || !/^[\x21-\x7e]{1,2048}$/.test(input.authorizationCode)))) {
    throw new Refusal('invalid-native-oauth-request');
  }
  return input;
}

async function loadNative(packageDir) {
  const metadata = JSON.parse(await readFile(path.join(packageDir, 'package.json'), 'utf8'));
  if (metadata.name !== 'openclaw' || metadata.version !== VERSION) {
    throw new Refusal('native-oauth-runtime-unsupported');
  }
  const file = path.join(packageDir, MODULE);
  if (createHash('sha256').update(await readFile(file)).digest('hex') !== SHA256) {
    throw new Refusal('native-oauth-runtime-unsupported');
  }
  return import(pathToFileURL(file));
}

async function operate(input) {
  const native = await loadNative(input.packageDir);
  const resolved = native.f(input.serverName, input.server);
  if (!resolved || resolved.kind !== 'http' || resolved.auth !== 'oauth' || resolved.sslVerify === false) {
    throw new Refusal('invalid-native-oauth-server');
  }
  const identity = {serverName: input.serverName, serverUrl: resolved.url};
  if (input.operation === 'status') return native.m(identity);
  if (input.operation === 'forget') {
    await native.p(identity);
    // Clearing the native local store is not provider-side revocation, nor
    // proof that an already running gateway has discarded cached credentials.
    return {forgotten: true};
  }
  // The host's existing one-shot listener owns this exact redirect. Never let
  // the native legacy fixed-port fallback send a Portal flow somewhere else.
  let redirect;
  try { redirect = new URL(resolved.oauth?.redirectUrl); }
  catch (error) {
    if (!(error instanceof TypeError)) throw error;
    throw new Refusal('invalid-native-oauth-redirect');
  }
  if (redirect.protocol !== 'http:' || redirect.hostname !== '127.0.0.1'
      || Number(redirect.port) < 1024 || Number(redirect.port) > 65535
      || redirect.username || redirect.password || redirect.pathname !== '/'
      || redirect.search || redirect.hash) {
    throw new Refusal('invalid-native-oauth-redirect');
  }
  let authUrl = null;
  const outcome = await native.h({...identity, config: resolved.oauth,
    authorizationCode: input.authorizationCode,
    fetchFn: native._({resourceUrl: resolved.url,
      headers: native.v(resolved.headers),
      fetchFn: native.g({resourceUrl: resolved.url, sslVerify: resolved.sslVerify,
        clientCert: resolved.clientCert, clientKey: resolved.clientKey})}),
    onAuthorizationUrl: url => { authUrl = url.toString(); },
  });
  if (input.operation === 'complete' && outcome !== 'authorized') {
    throw new Refusal('native-oauth-not-completed');
  }
  return {outcome, authUrl};
}

// One subprocess is the I/O boundary. Native errors can contain remote response
// bodies, URLs or tokens; only fixed ODS codes leave this boundary.
try {
  const result = await operate(await readRequest());
  const frame = JSON.stringify({ok: true, result});
  if (Buffer.byteLength(frame) > 8192) throw new Refusal('native-oauth-response-too-large');
  process.stdout.write(`${frame}\n`);
} catch (error) {
  const code = error instanceof Refusal ? error.message : 'native-oauth-failed';
  process.stdout.write(`${JSON.stringify({ok: false, code})}\n`);
  process.exitCode = 1;
}
