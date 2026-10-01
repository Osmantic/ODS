// Only controller-probed image facts, never host/sandbox inference. Keep the
// complete wheel compatibility relation small enough for deferred tool output.
const keys = (value, expected) => value && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).sort().join(',') === expected.split(',').sort().join(',');
const token = value => typeof value === 'string' && /^[A-Za-z0-9_.+\-]{1,128}$/.test(value);
const text = value => typeof value === 'string' && /^[\x20-\x7e]{0,128}$/.test(value);

export function validateProjectCapabilities(value) {
  if (Buffer.byteLength(JSON.stringify(value) ?? '') > 65536
      || value?.schemaVersion !== 1 || value.kind !== 'ods-project-capabilities'
      || value.runtime !== 'python' || value.scope !== 'installed-image-only') throw Error('invalid runtime evidence');
  if (value.status === 'unavailable') {
    if (!keys(value, 'schemaVersion,kind,runtime,scope,status,reason')
        || !['runtime-not-installed', 'runtime-probe-unavailable'].includes(value.reason)) throw Error('invalid unavailable evidence');
    return value;
  }
  if (value.status !== 'ready' || !keys(value, 'schemaVersion,kind,runtime,scope,status,image,python,platform,wheelCompatibility')
      || !/^sha256:[a-f0-9]{64}$/.test(value.image ?? '')
      || !keys(value.python, 'version,implementation,cacheTag,soabi') || !Object.values(value.python).every(token)
      || !keys(value.platform, 'os,machine,libc') || !token(value.platform.os) || !token(value.platform.machine)
      || !keys(value.platform.libc, 'name,version') || !Object.values(value.platform.libc).every(text)) throw Error('invalid runtime evidence');
  const wheels = value.wheelCompatibility;
  if (!keys(wheels, 'source,complete,tagCount,groups') || wheels.source !== 'pip._vendor.packaging.tags.sys_tags'
      || wheels.complete !== true || !Number.isInteger(wheels.tagCount) || wheels.tagCount < 1 || wheels.tagCount > 4096
      || !Array.isArray(wheels.groups) || !wheels.groups.length || wheels.groups.length > 128) throw Error('invalid wheel evidence');
  let count = 0;
  const pairs = new Set();
  for (const group of wheels.groups) {
    if (!keys(group, 'pythonTag,abiTag,platformTags') || !token(group.pythonTag) || !token(group.abiTag)
        || !/^[A-Za-z0-9_]+$/.test(group.pythonTag) || !/^[A-Za-z0-9_]+$/.test(group.abiTag)
        || !Array.isArray(group.platformTags) || !group.platformTags.length || group.platformTags.length > 256
        || !group.platformTags.every(token) || new Set(group.platformTags).size !== group.platformTags.length) throw Error('invalid wheel group');
    const pair = `${group.pythonTag}/${group.abiTag}`;
    if (pairs.has(pair)) throw Error('duplicate wheel group');
    pairs.add(pair); count += group.platformTags.length;
  }
  if (count !== wheels.tagCount) throw Error('incomplete wheel evidence');
  return value;
}

export function compactProjectCapabilities(value) {
  validateProjectCapabilities(value);
  if (value.status !== 'ready') return value;
  const platformSets = [], indexes = new Map(), groups = [];
  for (const {pythonTag, abiTag, platformTags} of value.wheelCompatibility.groups) {
    const key = JSON.stringify(platformTags);
    if (!indexes.has(key)) { indexes.set(key, platformSets.length); platformSets.push(platformTags); groups.push([]); }
    groups[indexes.get(key)].push(`${pythonTag}-${abiTag}`);
  }
  // CPython's pure-Python tags commonly repeat the native platform list plus
  // "any". Preserve that exact relation with an earlier-prefix reference,
  // instead of repeating every manylinux spelling in model context.
  const packedSets = platformSets.map((platforms, index) => {
    let base = -1;
    for (let candidate = 0; candidate < index; candidate++) {
      const prefix = platformSets[candidate];
      if (prefix.length < platforms.length && (base < 0 || prefix.length > platformSets[base].length)
          && prefix.every((platform, offset) => platforms[offset] === platform)) base = candidate;
    }
    return base < 0 ? platforms : {base, append: platforms.slice(platformSets[base].length)};
  });
  return {...value, wheelCompatibility: {...value.wheelCompatibility,
    groupFormat: 'groups[i]: pythonTag-abiTag pairs accepting platformSets[i]. A set {base,append} extends the earlier base set with append.',
    platformSets: packedSets, groups}};
}

export function projectCapabilityOutputLimit(config, agentId) {
  const agent = config?.agents?.list?.find(entry => entry?.id === agentId);
  const cap = (agent?.contextLimits ?? config?.agents?.defaults?.contextLimits)?.toolResultMaxChars;
  return Number.isSafeInteger(cap) && cap > 0 ? cap : 4000;
}

export function capabilityEnvelopeLength(tool, result) {
  const entry = {id: `openclaw:pixel-ods:${tool.name}`, source: 'openclaw', sourceName: 'pixel-ods',
    name: tool.name, label: tool.label ?? tool.name, description: tool.description};
  return JSON.stringify({tool: entry, result}, null, 2).length;
}

export function capabilityUnavailable(reason = 'runtime-probe-unavailable') {
  const evidence = {schemaVersion: 1, kind: 'ods-project-capabilities', runtime: 'python',
    scope: 'installed-image-only', status: 'unavailable', reason};
  return {isError: true, content: [{type: 'text', text:
    'Python runtime compatibility was not confirmed. No project job was submitted. Do not guess the architecture or wheel hashes; report this limitation or continue work that does not depend on these facts.'}], details: evidence};
}

export function capabilityToolResult(tool, raw, maxChars) {
  const value = compactProjectCapabilities(raw);
  if (value.status === 'unavailable') return capabilityUnavailable(value.reason);
  // Do not duplicate the full compatibility relation in details: Tool Search
  // serializes both fields. Truncation would corrupt architecture evidence.
  const result = {content: [{type: 'text', text: JSON.stringify(value)}],
    details: {schemaVersion: 1, kind: value.kind, runtime: value.runtime, status: value.status}};
  return capabilityEnvelopeLength(tool, result) <= maxChars - 128 ? result
    : capabilityUnavailable('capability-output-budget');
}
