import { deflateSync } from 'node:zlib';
import { WORKSPACE_EXPORT_HELPER } from './workspace-export-helper.mjs';

const MAX_PLAN_BYTES = 2_048;
const MAX_RESULT_CHARS = 3_500;
// Python's standard-library b85 decoder avoids a third-party runtime dependency.
const B85 = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz!#$%&()*+-;<=>?@^_`{|}~';
function base85(bytes) {
  let result = '';
  for (let offset = 0; offset < bytes.length; offset += 4) {
    const size = Math.min(4, bytes.length - offset);
    let value = 0;
    for (let i = 0; i < 4; i++) value = value * 256 + (bytes[offset + i] ?? 0);
    let part = '';
    for (let i = 0; i < 5; i++) { part = B85[value % 85] + part; value = Math.floor(value / 85); }
    result += part.slice(0, size + 1);
  }
  return result;
}
const pack = bytes => base85(deflateSync(bytes, {level: 9}));
const helper = pack(Buffer.from(WORKSPACE_EXPORT_HELPER, 'utf8'));
const record = value => value && typeof value === 'object' && !Array.isArray(value);
const keysWithin = (value, keys) => record(value) && Object.keys(value).every(key => keys.includes(key));

function relativePath(value) {
  return typeof value === 'string' && value.length > 0 && value.length <= 512 &&
    value === value.normalize('NFC') && !/[\\\u0000-\u001f\u007f]/u.test(value) &&
    value.split('/').length <= 12 && value.split('/').every(part =>
      part.length > 0 && part.length <= 128 && part !== '.' && part !== '..');
}

export function workspaceExportPlan(params) {
  if (!keysWithin(params, ['files', 'textMap']) || !Array.isArray(params.files) ||
      params.files.length < 1 || params.files.length > 16 ||
      Object.hasOwn(params, 'textMap') && !relativePath(params.textMap)) {
    throw new Error('Invalid export plan. Use explicit workspace-relative source and output paths.');
  }
  const hasMap = Object.hasOwn(params, 'textMap');
  const sources = new Set(), destinations = new Set(), mapKeys = new Set();
  for (const item of params.files) {
    if (!keysWithin(item, ['source', 'destination', 'key']) || !relativePath(item.source) ||
        sources.has(item.source.toLocaleLowerCase('en-US')) ||
        !Object.hasOwn(item, 'destination') && !hasMap ||
        Object.hasOwn(item, 'destination') && !relativePath(item.destination) ||
        hasMap && (typeof item.key !== 'string' || item.key.length < 1 || item.key.length > 128 ||
          /[\u0000-\u001f\u007f]/u.test(item.key) || mapKeys.has(item.key)) ||
        !hasMap && Object.hasOwn(item, 'key')) {
      throw new Error('Invalid or duplicate export file entry. Select distinct sources and explicit output paths and map keys.');
    }
    sources.add(item.source.toLocaleLowerCase('en-US'));
    if (hasMap) mapKeys.add(item.key);
    if (Object.hasOwn(item, 'destination')) {
      const key = item.destination.toLocaleLowerCase('en-US');
      if (destinations.has(key)) throw new Error('Export output paths collide.');
      destinations.add(key);
    }
  }
  if (hasMap && destinations.has(params.textMap.toLocaleLowerCase('en-US'))) {
    throw new Error('The text-map path collides with a copy destination.');
  }
  const encoded = Buffer.from(JSON.stringify(params), 'utf8');
  if (encoded.length > MAX_PLAN_BYTES) throw new Error('Export plan is too large; select a smaller explicit batch.');
  const command = `python3 -I -c 'import base64,zlib;exec(zlib.decompress(base64.b85decode("${helper}")))' '${pack(encoded)}'`;
  const plan = {
    executed: false,
    exec: { command, timeout: 30, yieldMs: 10_000 },
  };
  const text = JSON.stringify(plan);
  if (text.length > MAX_RESULT_CHARS) throw new Error('Export command exceeds the result budget; select a smaller explicit batch.');
  return {
    content: [{ type: 'text', text }],
  };
}

export function createWorkspaceExportPlanTool() {
  return {
    name: 'pixel_ods_workspace_export_plan',
    label: 'Export plan',
    description: 'Plan exact copies/UTF-8 JSON. Run returned ordinary exec at workspace root; not executed.',
    parameters: {
      type: 'object', additionalProperties: false, required: ['files'],
      properties: {
        files: { type: 'array', items: { type: 'object', additionalProperties: false, required: ['source'],
          properties: {
            source: { type: 'string', description: 'Existing regular file relative to the workspace root.' },
            destination: { type: 'string', description: 'Optional create-only raw-copy path relative to the workspace root.' },
            key: { type: 'string', description: 'Required unique JSON key only when textMap is selected.' },
          } } },
        textMap: { type: 'string', description: 'Optional create-only JSON file mapping the explicit keys to exact UTF-8 file text. Invalid UTF-8 fails before any output is written.' },
      },
    },
    async execute(_id, params, signal) {
      if (signal?.aborted) throw new Error('Export planning cancelled. No files were accessed.');
      return workspaceExportPlan(params);
    },
  };
}
