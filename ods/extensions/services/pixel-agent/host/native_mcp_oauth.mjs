// Owner-side adapter to the MCP OAuth implementation in ODS's pinned OpenClaw.
// The custodian must select the existing native server and bind the owner/state
// directory before launch. This is not a public endpoint or an enrollment API.
// Credentials and callback codes cross stdin, never argv or public diagnostics.
import {createHash} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import {writeSync} from 'node:fs';
import path from 'node:path';
import {Socket} from 'node:net';
import {isDeepStrictEqual} from 'node:util';
import {pathToFileURL} from 'node:url';

const VERSION = '2026.6.33';
// No public MCP OAuth SDK export exists at this pin. Refuse different bytes
// until the adapter is reviewed, rather than guessing another compiled chunk.
const MODULE = 'dist/agent-bundle-mcp-runtime-tW2Zz-uG.js';
const SHA256 = 'ed2e1d92189326625e7c8a735b83ede3942e1597e547587d8ee6413a7fd23bf0';
const OPERATIONS = new Set(['status', 'begin', 'complete', 'forget', 'probe', 'preflight']);
const CONFIG_OPERATIONS = new Set(['probe', 'preflight']);
const POLICY_MODULES = {
  materialize: ['dist/agent-bundle-mcp-materialize-D7HCVGV_.js',
    '823e4c938953e5ccbed137c4e9695a038ced9fc3293a7519d48421e4669bfd38'],
  effective: ['dist/effective-tool-policy-9Qtc69lV.js',
    'f21964ada299828fc53e4539cdb1b2abd13945edeb515c1f580c19277f7364bc'],
  sandbox: ['dist/config-BelhrjIc.js',
    'a371d72c78caf89fbfbd906973566c11067aa228d07c49b9e1ab849d11bbe4cb'],
  names: ['dist/agent-bundle-mcp-names-B9PLR-i_.js',
    '42770e13ab3410b4fbbb1eef88a99aa3d6e5cfccca1da7afaccfee2b65ccdd23'],
};
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
    ? 'authorizationCode,operation,packageDir,server,serverName' : CONFIG_OPERATIONS.has(input?.operation)
      ? 'configPath,configSha256,operation,packageDir,server,serverName' : 'operation,packageDir,server,serverName';
  if (!input || typeof input !== 'object' || Object.keys(input).sort().join(',') !== expected
      || !OPERATIONS.has(input.operation) || typeof input.packageDir !== 'string'
      || !path.isAbsolute(input.packageDir) || input.packageDir.includes('\0')
      || typeof input.serverName !== 'string' || !/^[a-z][a-z0-9-]{1,29}$/.test(input.serverName)
      || !input.server || typeof input.server !== 'object' || Array.isArray(input.server)
      || input.server.auth !== 'oauth'
      || (CONFIG_OPERATIONS.has(input.operation) && (typeof input.configPath !== 'string' || !path.isAbsolute(input.configPath)
        || input.configPath.includes('\0') || typeof input.configSha256 !== 'string'
        || !/^[a-f0-9]{64}$/.test(input.configSha256)))
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
  return loadPinnedModule(packageDir, MODULE, SHA256);
}

async function loadPinnedModule(packageDir, module, expected) {
  const file = path.join(packageDir, module);
  if (createHash('sha256').update(await readFile(file)).digest('hex') !== expected) {
    throw new Refusal('native-oauth-runtime-unsupported');
  }
  return import(pathToFileURL(file));
}

async function boundConfig(input) {
  const raw = await readFile(input.configPath);
  if (raw.length > 2 * 1024 * 1024 || createHash('sha256').update(raw).digest('hex') !== input.configSha256) {
    throw new Refusal('native-mcp-binding-changed');
  }
  const config = JSON.parse(raw.toString('utf8'));
  if (!isDeepStrictEqual(config?.mcp?.servers?.[input.serverName], input.server)) {
    throw new Refusal('native-mcp-binding-changed');
  }
  return config;
}

async function policyContext(input, config, native) {
  const include = input.server.toolFilter?.include;
  if (!Array.isArray(include) || !include.length || include.length > 32 || new Set(include).size !== include.length
      || include.some(name => typeof name !== 'string' || !/^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(name))) {
    throw new Refusal('native-app-exact-tool-filter-required');
  }
  const [materialize, effective, sandboxConfig, names] = await Promise.all(Object.values(POLICY_MODULES)
    .map(([module, sha]) => loadPinnedModule(input.packageDir, module, sha)));
  const agents = config?.agents?.list?.filter(agent => agent?.id === 'pixel');
  if (!agents || agents.length !== 1) throw new Refusal('native-app-policy-context-unavailable');
  const selected = agents[0].model ?? config?.agents?.defaults?.model;
  const model = typeof selected === 'string' ? selected : selected?.primary;
  const slash = typeof model === 'string' ? model.indexOf('/') : -1;
  const sandbox = sandboxConfig.i(config, 'pixel');
  if (slash < 1 || slash === model.length - 1 || !['all', 'off'].includes(sandbox.mode)) {
    throw new Refusal('native-app-policy-context-unavailable');
  }
  // The active gateway also merges enabled plugin bundles. Project its actual
  // namespace without connecting those servers, so a sanitized alias cannot
  // make a selected-only probe certify another server's qualified tool names.
  const loaded = native.y({cfg: config,
    workspaceDir: agents[0].workspace ?? config.agents.defaults?.workspace ?? path.dirname(input.configPath)});
  const used = new Set();
  let selectedName;
  for (const serverName of Object.keys(loaded.mcpServers)) {
    const safe = names.r(serverName, used);
    if (serverName === input.serverName) selectedName = safe;
  }
  const expected = include.map(name => `${input.serverName}__${name}`);
  const permitted = tools => {
    const allowed = effective.t({config, bundledTools: tools, agentId: 'pixel',
      sessionKey: 'agent:pixel:ods-app-readiness', modelProvider: model.slice(0, slash), modelId: model.slice(slash + 1),
      sandboxToolPolicy: sandbox.mode === 'all' ? sandbox.tools : undefined,
      toolPolicyAuditLogLevel: 'debug', warn: () => {}});
    return expected.every(name => allowed.some(tool => tool.name === name));
  };
  return {materialize, include, expected, permitted, namespaceValid: selectedName === input.serverName};
}

async function preflight(input, native) {
  const config = await boundConfig(input);
  const result = (eligible, reason) => ({eligible, reason, scope: 'pixel-owner-default'});
  if (input.server.enabled !== true) return result(false, 'disabled');
  const policy = await policyContext(input, config, native);
  if (!policy.namespaceValid) return result(false, 'namespace-conflict');
  // Project only the reviewed names as inventory-only native tools. This never
  // opens a server, looks up credentials, registers a client or runs a tool.
  // It proves eligibility of this candidate policy, not provider availability.
  const catalog = {servers: {[input.serverName]: {serverName: input.serverName, safeServerName: input.serverName}},
    tools: policy.include.map(toolName => ({serverName: input.serverName, safeServerName: input.serverName,
      toolName, inputSchema: {type: 'object', properties: {}}}))};
  const eligible = policy.permitted(policy.materialize.t({catalog, reservedToolNames: []}));
  return result(eligible, eligible ? 'eligible' : 'policy-blocked');
}

async function probe(input, native) {
  const config = await boundConfig(input);
  const result = (reachable, permitted, reason) => ({reachable, permitted, reason, scope: 'pixel-owner-default'});
  if (input.server.enabled === false) return result(false, false, 'disabled');
  // Presence is a prerequisite, never readiness proof. Do not let a passive
  // check register a fresh OAuth client or start another sign-in when absent.
  const saved = await native.m({serverName: input.serverName, serverUrl: input.server.url});
  if (!saved.hasTokens) return result(false, false, 'sign-in-required');
  const policy = await policyContext(input, config, native);
  if (!policy.namespaceValid) return result(false, false, 'namespace-conflict');
  // Only the selected explicit HTTP server is contacted. Do not load another
  // workspace/plugin MCP bundle or execute any tool just to check readiness.
  const runtime = native.n({sessionId: 'ods-app-readiness', sessionKey: 'agent:pixel:ods-app-readiness',
    workspaceDir: path.dirname(input.configPath), manifestRegistry: {plugins: []},
    cfg: {plugins: {enabled: false}, mcp: {servers: {[input.serverName]: input.server}}}});
  try {
    const catalog = await runtime.getCatalog();
    if (!catalog.servers[input.serverName]) return result(false, false, 'unreachable');
    const tools = policy.materialize.t({catalog, reservedToolNames: []});
    if (policy.expected.some(name => !tools.some(tool => tool.name === name))) {
      return result(true, false, 'tools-unavailable');
    }
    const permitted = policy.permitted(tools);
    return result(true, permitted, permitted ? 'ready' : 'policy-blocked');
  } finally { await runtime.dispose(); }
}

async function operate(input) {
  const native = await loadNative(input.packageDir);
  const resolved = native.f(input.serverName, input.server);
  if (!resolved || resolved.kind !== 'http' || resolved.auth !== 'oauth' || resolved.sslVerify === false) {
    throw new Refusal('invalid-native-oauth-server');
  }
  if (input.operation === 'probe') return probe(input, native);
  if (input.operation === 'preflight') return preflight(input, native);
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
let guardian, replyFd;
function emit(frame) {
  const raw = `${JSON.stringify(frame)}\n`;
  if (replyFd === undefined) process.stdout.write(raw);
  else writeSync(replyFd, raw);
}
try {
  if (process.env.ODS_NATIVE_OAUTH_REPLY_FD !== undefined) {
    const value = process.env.ODS_NATIVE_OAUTH_REPLY_FD;
    if (!/^[0-9]{1,6}$/.test(value) || Number(value) < 3) throw new Refusal('invalid-native-oauth-reply');
    replyFd = Number(value);
  }
  if (process.env.ODS_NATIVE_OAUTH_GUARD_FD !== undefined) {
    const value = process.env.ODS_NATIVE_OAUTH_GUARD_FD;
    if (!/^[0-9]{1,6}$/.test(value) || Number(value) < 3) throw new Refusal('invalid-native-oauth-guardian');
    guardian = new Socket({fd: Number(value), readable: true, writable: false});
    guardian.on('end', () => process.exit(1));
    guardian.on('error', () => process.exit(1));
    guardian.resume();
    guardian.unref();
  }
  const result = await operate(await readRequest());
  const frame = {ok: true, result};
  if (Buffer.byteLength(JSON.stringify(frame)) > 8192) throw new Refusal('native-oauth-response-too-large');
  emit(frame);
} catch (error) {
  const code = error instanceof Refusal ? error.message : 'native-oauth-failed';
  emit({ok: false, code});
  process.exitCode = 1;
} finally {
  guardian?.destroy();
}
