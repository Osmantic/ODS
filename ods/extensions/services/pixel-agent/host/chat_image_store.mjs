// Private, immutable copies of API-validated conversation images. This is not
// an upload endpoint or an image decoder: the Dashboard validates full pixels.
import fs from 'node:fs';
import path from 'node:path';
import {createHash, randomBytes} from 'node:crypto';

export const CHAT_IMAGE_BYTES = 8 * 1024 * 1024;
export const CHAT_IMAGE_CACHE_BYTES = 128 * 1024 * 1024;
const USER = /^ods-[a-f0-9]{64}$/;
const ID = /^img-[a-f0-9]{32}$/;
const SHA = /^[a-f0-9]{64}$/;
const MIMES = new Set(['image/png', 'image/jpeg', 'image/webp']);
const HEADER_BYTES = 1024;
const MAX_FILES = 4096;
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).sort().join(',') === keys.split(',').sort().join(',');
export class ChatImageError extends Error {
  constructor(code, status = 409) {super(code); this.code = code; this.status = status;}
}
const fail = (code, status) => {throw new ChatImageError(code, status);};

export function normalizeChatImageReference(value) {
  if (!exact(value, 'id,sha256') || typeof value.id !== 'string' || value.id.length !== 36 || !ID.test(value.id)
      || typeof value.sha256 !== 'string' || value.sha256.length !== 64 || !SHA.test(value.sha256)) fail('invalid-image-reference', 400);
  return {id:value.id, sha256:value.sha256};
}
function admitted(reference, refs) {
  if (!Array.isArray(refs) || refs.length > 8000) fail('image-not-admitted', 403);
  let found = false;
  for (const value of refs) {
    const item = normalizeChatImageReference(value);
    if (item.id === reference.id) {
      if (item.sha256 !== reference.sha256) fail('image-identity-conflict', 409);
      found = true;
    }
  }
  if (!found) fail('image-not-admitted', 403);
}
function signature(data, mimeType) {
  if (mimeType === 'image/png') return data.length >= 8 && data.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10]));
  if (mimeType === 'image/jpeg') return data.length >= 3 && data[0] === 255 && data[1] === 216 && data[2] === 255;
  return mimeType === 'image/webp' && data.length >= 12 && data.toString('ascii',0,4) === 'RIFF' && data.toString('ascii',8,12) === 'WEBP';
}
function normalizeImage(value) {
  if (!exact(value, 'id,sha256,mimeType,data')) fail('invalid-image-payload', 400);
  const reference = normalizeChatImageReference({id:value.id, sha256:value.sha256});
  if (!MIMES.has(value.mimeType) || !Buffer.isBuffer(value.data) || !value.data.length
      || value.data.length > CHAT_IMAGE_BYTES || !signature(value.data, value.mimeType)) fail('invalid-image-payload', 400);
  if (hash(value.data) !== reference.sha256) fail('image-digest-mismatch', 400);
  return {...reference, mimeType:value.mimeType, data:Buffer.from(value.data)};
}
function privateStat(file, directory = false) {
  const stat = fs.lstatSync(file);
  if (stat.isSymbolicLink() || (directory ? !stat.isDirectory() : !stat.isFile() || stat.nlink !== 1)
      || process.platform !== 'win32' && (stat.uid !== process.getuid() || stat.mode & 0o077)) fail('image-storage-unavailable', 503);
  return stat;
}
function prepareDirectory(directory) {
  if (typeof directory !== 'string' || !path.isAbsolute(directory) || path.resolve(directory) !== directory
      || directory === path.parse(directory).root) fail('image-storage-unavailable', 503);
  let current = path.parse(directory).root;
  for (const component of directory.slice(current.length).split(path.sep)) {
    current = path.join(current, component);
    try {fs.mkdirSync(current, {mode:0o700});} catch (error) {if (error.code !== 'EEXIST') throw error;}
    const stat = fs.lstatSync(current);
    if (!stat.isDirectory() || stat.isSymbolicLink()) fail('image-storage-unavailable', 503);
  }
  privateStat(directory, true);
}
function encode(user, image) {
  const metadata = Buffer.from(JSON.stringify({schemaVersion:1, user, id:image.id, sha256:image.sha256,
    mimeType:image.mimeType, bytes:image.data.length}));
  if (metadata.length > HEADER_BYTES) fail('invalid-image-payload', 400);
  const header = Buffer.alloc(4); header.writeUInt32BE(metadata.length);
  return Buffer.concat([header, metadata, image.data]);
}
function readRecord(file, user, reference) {
  const before = privateStat(file);
  if (before.size < 5 || before.size > CHAT_IMAGE_BYTES + HEADER_BYTES + 4) fail('image-storage-unavailable', 503);
  const fd = fs.openSync(file, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0) | (fs.constants.O_NONBLOCK || 0));
  let bytes;
  try {
    const held = fs.fstatSync(fd);
    if (held.dev !== before.dev || held.ino !== before.ino || held.size !== before.size) fail('image-storage-unavailable', 503);
    const bounded = Buffer.alloc(held.size + 1);
    let count = 0;
    while (count < bounded.length) {
      const read = fs.readSync(fd, bounded, count, bounded.length - count, null);
      if (!read) break;
      count += read;
    }
    bytes = bounded.subarray(0, count);
    const after = fs.fstatSync(fd);
    if (after.size !== held.size || after.mtimeMs !== held.mtimeMs || bytes.length !== held.size) fail('image-storage-unavailable', 503);
  } finally {fs.closeSync(fd);}
  const length = bytes.readUInt32BE(0);
  if (!length || length > HEADER_BYTES || length + 4 >= bytes.length) fail('image-storage-unavailable', 503);
  let metadata;
  try {metadata = JSON.parse(bytes.toString('utf8', 4, length + 4));} catch {fail('image-storage-unavailable', 503);}
  const data = bytes.subarray(length + 4);
  if (!exact(metadata, 'schemaVersion,user,id,sha256,mimeType,bytes') || metadata.schemaVersion !== 1
      || metadata.user !== user || metadata.id !== reference.id || metadata.sha256 !== reference.sha256
      || metadata.bytes !== data.length || !MIMES.has(metadata.mimeType) || !signature(data, metadata.mimeType)
      || hash(data) !== reference.sha256) fail('image-identity-conflict', 409);
  return {id:metadata.id, sha256:metadata.sha256, mimeType:metadata.mimeType, bytes:data.length, data};
}

export function createChatImageStore(directory, {maxBytes = CHAT_IMAGE_CACHE_BYTES} = {}) {
  if (!Number.isSafeInteger(maxBytes) || maxBytes < 1 || maxBytes > CHAT_IMAGE_CACHE_BYTES) fail('invalid-image-storage-limit', 400);
  prepareDirectory(directory);
  function filename(user, reference) {
    if (typeof user !== 'string' || user.length !== 68 || !USER.test(user)) fail('invalid-image-user', 400);
    normalizeChatImageReference({id:reference.id, sha256:reference.sha256});
    privateStat(directory, true);
    return path.join(directory, `${user}--${reference.id}.image`);
  }
  function read(user, reference, refs) {
    normalizeChatImageReference(reference); admitted(reference, refs);
    try {return readRecord(filename(user, reference), user, reference);}
    catch (error) {if (error.code === 'ENOENT') fail('image-copy-unavailable', 404); if (error instanceof ChatImageError) throw error; fail('image-storage-unavailable', 503);}
  }
  function put(user, values, refs) {
    if (!Array.isArray(values) || !values.length || values.length > 4) fail('invalid-image-count', 400);
    const images = values.map(normalizeImage), seen = new Set();
    let total = 0;
    const records = images.map(image => {
      if (seen.has(image.id)) fail('duplicate-image-reference', 400);
      seen.add(image.id); admitted(image, refs); total += image.data.length;
      return {image, file:filename(user, image), bytes:encode(user, image)};
    });
    if (total > CHAT_IMAGE_BYTES) fail('image-turn-too-large', 413);
    const lock = path.join(directory, '.write-lock');
    try {fs.mkdirSync(lock, {mode:0o700});} catch (error) {if (error.code === 'EEXIST') fail('image-storage-busy', 503); throw error;}
    try {
      const entries = fs.readdirSync(directory).filter(name => name !== '.write-lock');
      if (entries.length > MAX_FILES) fail('image-storage-limit', 507);
      let occupied = 0;
      for (const name of entries) {
        if (![112,149].includes(name.length) || !/^ods-[a-f0-9]{64}--img-[a-f0-9]{32}\.image(?:\.tmp-[a-f0-9]{32})?$/.test(name)) fail('image-storage-unavailable', 503);
        occupied += privateStat(path.join(directory, name)).size;
      }
      const pending = records.filter(record => {
        try {
          const current = readRecord(record.file, user, record.image);
          if (current.mimeType !== record.image.mimeType || !current.data.equals(record.image.data)) fail('image-identity-conflict', 409);
          return false;
        } catch (error) {if (error.code === 'ENOENT') return true; throw error;}
      });
      if (entries.length + pending.length > MAX_FILES || occupied + pending.reduce((sum, record) => sum + record.bytes.length, 0) > maxBytes) fail('image-storage-limit', 507);
      for (const record of pending) {
        const temporary = `${record.file}.tmp-${randomBytes(16).toString('hex')}`;
        let fd;
        try {
          fd = fs.openSync(temporary, 'wx', 0o600);
          fs.writeFileSync(fd, record.bytes); fs.fsyncSync(fd); fs.closeSync(fd); fd = undefined;
          // No overwrite, including another process's already published receipt.
          fs.linkSync(temporary, record.file);
        } finally {
          if (fd !== undefined) fs.closeSync(fd);
          try {fs.unlinkSync(temporary);} catch (error) {if (error.code !== 'ENOENT') throw error;}
        }
      }
      let directoryFd;
      try {directoryFd = fs.openSync(directory, 'r'); fs.fsyncSync(directoryFd);}
      catch (error) {if (!['EINVAL','ENOTSUP','EBADF','EISDIR','EPERM','EACCES'].includes(error.code)) throw error;}
      finally {if (directoryFd !== undefined) fs.closeSync(directoryFd);}
      return images.map(({id, sha256}) => ({id, sha256}));
    } catch (error) {if (error instanceof ChatImageError) throw error; fail('image-storage-unavailable', 503);}
    finally {fs.rmdirSync(lock);}
  }
  return {put, read};
}

// Called only after ingress authentication. The user comes from the trusted
// route/session binding; the model supplies only the two reference fields.
export function createChatImageReadHandler(store, readAdmittedRefs) {
  return async (user, request) => {
    if (typeof user !== 'string' || user.length !== 68 || !USER.test(user)) fail('invalid-image-user', 400);
    const reference = normalizeChatImageReference(request);
    const refs = await readAdmittedRefs(user);
    const image = store.read(user, reference, refs);
    return {schemaVersion:1, image:{...image, data:image.data.toString('base64')}};
  };
}
