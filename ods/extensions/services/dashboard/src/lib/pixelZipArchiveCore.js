import { configure, Uint8ArrayReader, ZipReader } from '@zip.js/zip.js/lib/zip-core-custom.js'
import { Inflate } from 'fflate'
import { sha256 as digest } from './pixelArtifacts'

export const ZIP_LIMITS = Object.freeze({ compressedBytes: 4 * 1024 * 1024, entries: 64, memberBytes: 256 * 1024, selectedBytes: 1024 * 1024, deadlineMs: 5000 })
const TEXT_EXTENSION = /\.(txt|md|markdown|csv|tsv|json|jsonl|yaml|yml|toml|xml|html|css|js|jsx|ts|tsx|py|sh|log)$/i
const fail = message => { throw new Error(message) }

// zip.js validates the ZIP structure and CRC. fflate supplies only the raw
// DEFLATE codec, with bounded input chunks and output checked before enqueue.
// No nested workers, WASM fetches or remote codec URLs are used.
class BoundedInflateStream {
  constructor(format) {
    if (format !== 'deflate-raw') throw new Error('Unsupported compression format')
    let inflater, outputBytes = 0
    return new globalThis.TransformStream({
      start(controller) {
        inflater = new Inflate(chunk => {
          outputBytes += chunk.byteLength
          if (outputBytes > ZIP_LIMITS.memberBytes) fail('ZIP member exceeds the 256 KB expanded text limit.')
          controller.enqueue(chunk)
        })
      },
      transform(chunk) {
        for (let offset = 0; offset < chunk.byteLength; offset += 1024) inflater.push(chunk.subarray(offset, offset + 1024), false)
      },
      flush() { inflater.push(new Uint8Array(), true) },
    })
  }
}
configure({ useWebWorkers: false, useCompressionStream: false, chunkSize: 1024, DecompressionStreamFallback: BoundedInflateStream })

function rejectZip64Footer(bytes) {
  // Format exclusion only; zip.js owns all ZIP decoding/validation below.
  // Its entry.zip64 flag does not include archive-only ZIP64 end records.
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
  for (let offset = bytes.length - 22; offset >= Math.max(0, bytes.length - 65557); offset--) {
    if (view.getUint32(offset, true) !== 0x06054b50 || offset + 22 + view.getUint16(offset + 20, true) !== bytes.length) continue
    if ([4, 6, 8, 10].some(field => view.getUint16(offset + field, true) === 0xffff) || [12, 16].some(field => view.getUint32(offset + field, true) === 0xffffffff) || (offset >= 20 && view.getUint32(offset - 20, true) === 0x07064b50)) fail('ZIP64 archives are not supported.')
  }
}

function safePath(entry) {
  const path = entry.filename
  if (!path || path.length > 512 || /[\\:]/.test(path) || [...path].some(char => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127) || path.startsWith('/')) fail('ZIP contains an unsafe or oversized path.')
  const parts = (entry.directory ? path.replace(/\/$/, '') : path).split('/')
  if (parts.some(part => !part || part === '.' || part === '..')) fail('ZIP contains an unsafe path.')
  return parts.join('/').normalize('NFC').toLowerCase()
}

function metadata(entry) {
  if (entry.encrypted || entry.zipCrypto) fail('Encrypted ZIP archives are not supported.')
  if (entry.zip64 || entry.diskNumberStart !== 0) fail('ZIP64 and split ZIP archives are not supported.')
  if (![0, 8].includes(entry.compressionMethod)) fail('Only stored and DEFLATE ZIP members are supported.')
  const mode = (entry.externalFileAttributes >>> 16) & 0xf000
  if (entry.symlink || (mode && mode !== (entry.directory ? 0x4000 : 0x8000))) fail('ZIP links and special files are not supported.')
  if (![entry.compressedSize, entry.uncompressedSize].every(size => Number.isSafeInteger(size) && size >= 0)) fail('ZIP member has invalid sizes.')
  const item = { path: entry.filename, bytes: entry.uncompressedSize, kind: 'text' }
  if (entry.directory) item.kind = 'directory'
  else if (!TEXT_EXTENSION.test(entry.filename)) { item.kind = 'unread'; item.reason = 'Binary or unsupported file type; not read.' }
  else if (entry.uncompressedSize > ZIP_LIMITS.memberBytes) { item.kind = 'unread'; item.reason = 'Exceeds the 256 KB expanded text limit; not read.' }
  return item
}

// Runs only inside the disposable worker in production. Metadata inspection
// never inflates members. Extraction returns nothing until every selection has
// passed actual-size, CRC and fatal UTF-8 validation.
export async function processZipArchive(bytes, name, selectedPaths = null) {
  if (!(bytes instanceof Uint8Array) || !bytes.byteLength || bytes.byteLength > ZIP_LIMITS.compressedBytes) fail('Choose a nonempty ZIP no larger than 4 MB.')
  rejectZip64Footer(bytes)
  if (typeof name !== 'string' || name.length > 255) fail('ZIP filename is too long.')
  if (selectedPaths !== null && (!Array.isArray(selectedPaths) || !selectedPaths.length || selectedPaths.length > ZIP_LIMITS.entries || selectedPaths.some(path => typeof path !== 'string') || new Set(selectedPaths).size !== selectedPaths.length)) fail('Select distinct text files from the ZIP.')
  const reader = new ZipReader(new Uint8ArrayReader(bytes), { strictness: 'strict', checkCrc32: true, checkOverlappingEntry: true, useWebWorkers: false, useCompressionStream: false })
  try {
    const entries = [], handles = new Map(), names = new Map()
    for await (const entry of reader.getEntriesGenerator()) {
      if (entries.length >= ZIP_LIMITS.entries) fail('ZIP contains more than 64 entries.')
      const key = safePath(entry), item = metadata(entry)
      if (names.has(key)) fail('ZIP contains duplicate or ambiguous paths.')
      names.set(key, item.kind === 'directory')
      entries.push(item); handles.set(item.path, entry)
      await entry.getData(new globalThis.WritableStream(), { checkOverlappingEntryOnly: true })
    }
    for (const key of names.keys()) {
      const parts = key.split('/')
      for (let end = 1; end < parts.length; end++) if (names.get(parts.slice(0, end).join('/')) === false) fail('ZIP contains conflicting file and directory paths.')
    }
    const result = { name, sha256: await digest(bytes), compressedBytes: bytes.byteLength, entries }
    if (selectedPaths === null) return result
    let total = 0
    const files = []
    for (const path of selectedPaths) {
      const item = entries.find(entry => entry.path === path)
      if (!item || item.kind !== 'text') fail('Only listed text candidates can be selected.')
      if (total + item.bytes > ZIP_LIMITS.selectedBytes) fail('Selected text exceeds the 1 MB expanded limit.')
      const chunks = []; let size = 0
      await handles.get(path).getData(new globalThis.WritableStream({ write(chunk) {
        size += chunk.byteLength; total += chunk.byteLength
        if (size > ZIP_LIMITS.memberBytes || total > ZIP_LIMITS.selectedBytes) fail('Selected text exceeds the expanded size limit.')
        chunks.push(chunk.slice())
      } }), { checkCrc32: true })
      if (size !== item.bytes) fail('ZIP member size does not match its metadata.')
      const data = new Uint8Array(size); let offset = 0
      for (const chunk of chunks) { data.set(chunk, offset); offset += chunk.length }
      let text
      try { text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(data) } catch { fail('Selected ZIP text must be valid UTF-8.') }
      if (text.includes('\0')) fail('Selected ZIP text contains binary NUL bytes.')
      files.push({ path, bytes: size, sha256: await digest(data), text })
    }
    return { ...result, files }
  } finally { await reader.close() }
}
