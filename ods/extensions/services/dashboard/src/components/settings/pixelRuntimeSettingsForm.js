export const CONTROLS = {
  contextTokens: { type: 'integer', min: 4096, max: 10000000 },
  maxOutputTokens: { type: 'integer', min: 1, max: 10000000 },
  compactionMode: { type: 'enum', choices: ['default', 'safeguard'] },
  compactionReserveTokens: { type: 'integer', min: 0, max: 10000000 },
  compactionReserveFloorTokens: { type: 'integer', min: 0, max: 10000000 },
  compactionKeepRecentTokens: { type: 'integer', min: 1, max: 10000000 },
  compactionHistoryShare: { type: 'number', min: 0.1, max: 0.9 },
  compactionRecentTurns: { type: 'integer', min: 0, max: 12 },
  compactionTimeoutSeconds: { type: 'integer', min: 1, max: 3600 },
  compactionNotify: { type: 'boolean' },
  compactionMemoryFlush: { type: 'boolean' },
  thinking: { type: 'enum', choices: ['off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'adaptive', 'max'] },
  verbosity: { type: 'enum', choices: ['off', 'on', 'full'] },
  reasoningVisibility: { type: 'enum', choices: ['off', 'on', 'stream'] },
  toolProgress: { type: 'enum', choices: ['explain', 'raw'] },
  temperature: { type: 'number', min: 0, max: 2 },
  topP: { type: 'number', min: 0.000001, max: 1 },
  toolResultMaxChars: { type: 'integer', min: 1, max: 2000000 },
  bootstrapMaxChars: { type: 'integer', min: 1, max: 2000000 },
  bootstrapTotalMaxChars: { type: 'integer', min: 1, max: 2000000 },
};

export const GROUPS = [
  {
    id: 'context',
    label: 'Context & Output',
    controls: ['contextTokens', 'maxOutputTokens'],
    labels: {
      contextTokens: 'Context Window Size',
      maxOutputTokens: 'Max Output Tokens',
    },
    help: {
      contextTokens: 'Maximum tokens the model may see. This is a request cap, not a backend memory allocation.',
    },
  },
  {
    id: 'compaction',
    label: 'Compaction',
    controls: [
      'compactionMode', 'compactionReserveTokens', 'compactionReserveFloorTokens',
      'compactionKeepRecentTokens', 'compactionHistoryShare', 'compactionRecentTurns',
      'compactionTimeoutSeconds', 'compactionNotify', 'compactionMemoryFlush',
    ],
    labels: {
      compactionMode: 'Compaction Mode',
      compactionReserveTokens: 'Reserve Tokens',
      compactionReserveFloorTokens: 'Reserve Floor Tokens',
      compactionKeepRecentTokens: 'Keep Recent Tokens',
      compactionHistoryShare: 'History Share Ratio',
      compactionRecentTurns: 'Recent Turns to Keep',
      compactionTimeoutSeconds: 'Compaction Timeout',
      compactionNotify: 'Notify on Compaction',
      compactionMemoryFlush: 'Flush Memory Before Compaction',
    },
    help: {
      compactionMemoryFlush: 'Saves durable memory before compaction runs; does not delete memory.',
    },
  },
  {
    id: 'thinking',
    label: 'Thinking & Reasoning',
    controls: ['thinking', 'verbosity', 'reasoningVisibility'],
    labels: {
      thinking: 'Thinking Level',
      verbosity: 'Verbosity',
      reasoningVisibility: 'Reasoning visibility',
    },
    help: {
      thinking: 'Requested reasoning effort, subject to model support. Saving alone does not apply it.',
      reasoningVisibility: 'Provider-supplied reasoning when available, separately from progress summaries. No hidden prompts or fabricated thoughts.',
      verbosity: 'Runtime tool-output detail level; does not guarantee longer prose.',
    },
  },
  {
    id: 'generation',
    label: 'Generation & Tools',
    controls: [
      'toolProgress', 'temperature', 'topP',
      'toolResultMaxChars', 'bootstrapMaxChars', 'bootstrapTotalMaxChars',
    ],
    labels: {
      toolProgress: 'Tool Progress Display',
      temperature: 'Temperature',
      topP: 'Top P',
      toolResultMaxChars: 'Tool Result Max Chars',
      bootstrapMaxChars: 'Bootstrap Max Chars',
      bootstrapTotalMaxChars: 'Bootstrap Total Max Chars',
    },
    help: {
      toolProgress: 'Actual tool activity with redaction; raw is still redacted, not private args.',
    },
  },
];

const ALLOWED = new Set(Object.keys(CONTROLS));
const DANGEROUS = new Set(['constructor', 'toString', '__proto__']);

function isPlainObject(v) {
  return v !== null && typeof v === 'object' && [Object.prototype, null].includes(Object.getPrototypeOf(v));
}

function isSafeKey(k) {
  return ALLOWED.has(k) && !DANGEROUS.has(k);
}

function validateValue(controlName, value) {
  if (value === null) return true;
  const def = CONTROLS[controlName];
  if (!def) return false;
  if (def.type === 'boolean') {
    return value === true || value === false;
  }
  if (def.type === 'enum') {
    return def.choices.includes(value);
  }
  if (typeof value !== 'number') return false;
  const num = value;
  if (!Number.isFinite(num)) return false;
  if (def.min !== undefined && num < def.min) return false;
  if (def.max !== undefined && num > def.max) return false;
  if (def.type === 'integer' && (num !== Math.floor(num) || Math.abs(num) > Number.MAX_SAFE_INTEGER)) return false;
  return true;
}

function validatePreferences(preferences) {
  if (!isPlainObject(preferences)) return false;
  for (const [k, v] of Object.entries(preferences)) {
    if (!isSafeKey(k)) return false;
    if (v !== null && !validateValue(k, v)) return false;
  }
  return true;
}

function validateSnapshot(snapshot) {
  if (!isPlainObject(snapshot) || Object.keys(snapshot).length !== 2 || !Object.hasOwn(snapshot, 'revision') || !Object.hasOwn(snapshot, 'preferences')) return false;
  if (typeof snapshot.revision !== 'number' || !Number.isInteger(snapshot.revision) || snapshot.revision < 0 || snapshot.revision > Number.MAX_SAFE_INTEGER) return false;
  if (!isPlainObject(snapshot.preferences)) return false;
  if (!validatePreferences(snapshot.preferences)) return false;
  return true;
}

export function readSettings(payload, expectedRevision) {
  if (!isPlainObject(payload)) throw new Error('Malformed settings payload');
  if (!isPlainObject(payload.configuration)) throw new Error('Missing configuration');
  if (payload.configuration.schemaVersion !== 1) throw new Error('Unsupported schema version');
  if (typeof payload.configuration.revision !== 'number' || !Number.isInteger(payload.configuration.revision)) throw new Error('Invalid revision');
  if (payload.configuration.revision < 0 || payload.configuration.revision > Number.MAX_SAFE_INTEGER) throw new Error('Revision out of range');
  if (!validatePreferences(payload.configuration.preferences)) throw new Error('Invalid preferences');
  if (!isPlainObject(payload.runtime)) throw new Error('Missing runtime');
  const oldHost = payload.runtime.status === 'not-applied' && payload.runtime.reason === 'settings-runtime-not-integrated';
  const currentHost = payload.runtime.status === 'not-inspected' && payload.runtime.reason === 'runtime-status-separate';
  if (!oldHost && !currentHost) throw new Error('Unexpected persistence runtime marker');
  const allowedRoot = new Set(['configuration', 'runtime']);
  for (const k of Object.keys(payload)) {
    if (!allowedRoot.has(k)) throw new Error(`Unknown root field: ${k}`);
  }
  const allowedConfig = new Set(['schemaVersion', 'revision', 'preferences']);
  for (const k of Object.keys(payload.configuration)) {
    if (!allowedConfig.has(k)) throw new Error(`Unknown config field: ${k}`);
  }
  const allowedRuntime = new Set(['status', 'reason']);
  for (const k of Object.keys(payload.runtime)) {
    if (!allowedRuntime.has(k)) throw new Error(`Unknown runtime field: ${k}`);
  }
  if (expectedRevision !== undefined && expectedRevision !== payload.configuration.revision) {
    throw new Error('Revision mismatch');
  }
  const prefsCopy = {};
  for (const k of Object.keys(payload.configuration.preferences)) {
    prefsCopy[k] = payload.configuration.preferences[k];
  }
  return { revision: payload.configuration.revision, preferences: prefsCopy };
}

function parseChange(controlName, rawStr) {
  if (rawStr === '') return null;
  const def = CONTROLS[controlName];
  if (!def) throw new Error(`Unknown control: ${controlName}`);
  if (def.type === 'boolean') {
    if (rawStr === 'true') return true;
    if (rawStr === 'false') return false;
    throw new Error(`Invalid boolean for ${controlName}: "${rawStr}"`);
  }
  if (def.type === 'enum') {
    if (!def.choices.includes(rawStr)) throw new Error(`Invalid choice for ${controlName}`);
    return rawStr;
  }
  if (/\s/.test(rawStr)) throw new Error(`Whitespace-only value for ${controlName}`);
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(rawStr)) throw new Error(`Non-numeric value for ${controlName}: "${rawStr}"`);
  const num = Number(rawStr);
  if (!Number.isFinite(num)) throw new Error(`Non-finite for ${controlName}`);
  if (def.type === 'integer' && (num !== Math.floor(num) || Math.abs(num) > Number.MAX_SAFE_INTEGER)) {
    throw new Error(`Invalid integer for ${controlName}`);
  }
  if (def.min !== undefined && num < def.min) throw new Error(`Below minimum for ${controlName}`);
  if (def.max !== undefined && num > def.max) throw new Error(`Above maximum for ${controlName}`);
  return num;
}

export function prepareSettingsSave(snapshot, rawChanges) {
  if (!validateSnapshot(snapshot)) throw new Error('Invalid snapshot');
  if (typeof snapshot.revision !== 'number' || snapshot.revision < 0 || snapshot.revision > Number.MAX_SAFE_INTEGER) throw new Error('Invalid snapshot revision');
  if (snapshot.revision === Number.MAX_SAFE_INTEGER) throw new Error('Revision at maximum');
  if (!isPlainObject(rawChanges) || Array.isArray(rawChanges)) throw new Error('Changes must be a plain object');
  if (Object.keys(rawChanges).length === 0) throw new Error('Changes must be non-empty');
  for (const [k, v] of Object.entries(rawChanges)) {
    if (!isSafeKey(k)) throw new Error(`Invalid control key: ${k}`);
    if (typeof v !== 'string') throw new Error(`Change value must be string for ${k}`);
  }
  const parsed = {};
  for (const [k, raw] of Object.entries(rawChanges)) {
    parsed[k] = parseChange(k, raw);
  }
  return { expectedRevision: snapshot.revision, changes: parsed };
}
