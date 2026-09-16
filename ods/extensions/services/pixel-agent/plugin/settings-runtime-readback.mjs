// Fixed, presence-aware current-runtime-config readback, not effective model limits.
// The existing admission owner must hold its lease before calling this helper.
const invalid = () => new Error('invalid-runtime-settings');
function object(value) {
  if (!value || typeof value !== 'object' ||
      ![Object.prototype, null].includes(Object.getPrototypeOf(value))) throw invalid();
  return value;
}
function own(value, key) {
  object(value);
  const descriptor = Object.getOwnPropertyDescriptor(value, key);
  if (!descriptor) return {present: false, value: null};
  if (!Object.hasOwn(descriptor, 'value')) throw invalid();
  return {present: true, value: descriptor.value};
}
function lookup(root, path) {
  let found = {present: true, value: root};
  for (const key of path) {
    if (!found.present) return found;
    found = own(found.value, key);
  }
  return found;
}
const integer = (min, max) => value => Number.isSafeInteger(value) && value >= min && value <= max;
const number = (min, max) => value => typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max;
const choice = (...allowed) => value => typeof value === 'string' && allowed.includes(value);
const boolean = value => typeof value === 'boolean';
const specs = [
  ['contextTokens', 'pixel', ['contextTokens'], integer(4096, 10000000)],
  ['maxOutputTokens', 'pixel', ['params', 'maxTokens'], integer(1, 10000000)],
  ['thinking', 'pixel', ['thinkingDefault'], choice('off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'adaptive', 'max')],
  ['verbosity', 'pixel', ['verboseDefault'], choice('off', 'on', 'full')],
  ['reasoningVisibility', 'pixel', ['reasoningDefault'], choice('off', 'on', 'stream')],
  ['toolProgress', 'pixel', ['toolProgressDetail'], choice('explain', 'raw')],
  ['temperature', 'pixel', ['params', 'temperature'], number(0, 2)],
  ['topP', 'pixel', ['params', 'topP'], number(0.000001, 1)],
  ['toolResultMaxChars', 'pixel', ['contextLimits', 'toolResultMaxChars'], integer(1, 2000000)],
  ['bootstrapMaxChars', 'pixel', ['bootstrapMaxChars'], integer(1, 2000000)],
  ['bootstrapTotalMaxChars', 'pixel', ['bootstrapTotalMaxChars'], integer(1, 2000000)],
  ['compactionMode', 'defaults', ['compaction', 'mode'], choice('default', 'safeguard')],
  ['compactionReserveTokens', 'defaults', ['compaction', 'reserveTokens'], integer(0, 10000000)],
  ['compactionReserveFloorTokens', 'defaults', ['compaction', 'reserveTokensFloor'], integer(0, 10000000)],
  ['compactionKeepRecentTokens', 'defaults', ['compaction', 'keepRecentTokens'], integer(1, 10000000)],
  ['compactionHistoryShare', 'defaults', ['compaction', 'maxHistoryShare'], number(0.1, 0.9)],
  ['compactionRecentTurns', 'defaults', ['compaction', 'recentTurnsPreserve'], integer(0, 12)],
  ['compactionTimeoutSeconds', 'defaults', ['compaction', 'timeoutSeconds'], integer(1, 3600)],
  ['compactionNotify', 'defaults', ['compaction', 'notifyUser'], boolean],
  ['compactionMemoryFlush', 'defaults', ['compaction', 'memoryFlush', 'enabled'], boolean],
];
function field(root, path, valid) {
  const result = root.present ? lookup(root.value, path) : {present: false, value: null};
  if (result.present && !valid(result.value)) throw invalid();
  return result;
}
function optionalObject(root, path) {
  const result = lookup(root, path);
  if (result.present) object(result.value);
  return result;
}

export function readRuntimeSettings(config, metadata) {
  object(metadata);
  const read = key => own(metadata, key).value;
  const pid = read('pid'), runtimeVersion = read('runtimeVersion');
  const revision = read('revision'), observedAt = read('observedAt');
  if (!integer(1, Number.MAX_SAFE_INTEGER)(pid) || typeof runtimeVersion !== 'string' ||
      !runtimeVersion.trim() || runtimeVersion.length > 128 || /[\x00-\x1f\x7f]/.test(runtimeVersion) ||
      typeof revision !== 'string' || !/^[a-f0-9]{64}$/.test(revision) ||
      typeof observedAt !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(observedAt) ||
      !Number.isFinite(Date.parse(observedAt)) || new Date(observedAt).toISOString() !== observedAt) throw invalid();
  const agents = lookup(config, ['agents', 'list']).value;
  if (!Array.isArray(agents) || !agents.length) throw invalid();
  const ids = new Set();
  let pixel;
  for (let index = 0; index < agents.length; index++) {
    // Arrays may contain holes or accessor entries; do not evaluate either.
    const descriptor = Object.getOwnPropertyDescriptor(agents, String(index));
    if (!descriptor || !Object.hasOwn(descriptor, 'value')) throw invalid();
    const agent = object(descriptor.value), id = own(agent, 'id').value;
    if (typeof id !== 'string' || !id.trim() || ids.has(id)) throw invalid();
    ids.add(id);
    if (id === 'pixel') pixel = agent;
  }
  if (!pixel) throw invalid();
  const roots = {pixel: {present: true, value: pixel}, defaults: optionalObject(config, ['agents', 'defaults'])};
  const plugin = optionalObject(config, ['plugins', 'entries', 'pixel-ods', 'config']);
  const fields = {};
  for (const [name, scope, path, valid] of specs) fields[name] = field(roots[scope], path, valid);
  return {schemaVersion: 1, source: 'current-runtime-config', pid, runtimeVersion, revision, observedAt,
    pixelOnlyRuntime: agents.length === 1, fields,
    pluginContext: field(plugin, ['modelContextWindow'], integer(4096, 10000000))};
}
