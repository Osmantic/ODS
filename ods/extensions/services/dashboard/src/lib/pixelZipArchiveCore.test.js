// @vitest-environment node
import { describe, it, expect, vi } from 'vitest'
import { createHash } from 'node:crypto'
import { Buffer } from 'node:buffer'
import { zipSync, strToU8 } from 'fflate'
import { processZipArchive, ZIP_LIMITS } from './pixelZipArchiveCore'

const zip = (files, options) => zipSync(Object.fromEntries(Object.entries(files).map(([name, value]) => [name, typeof value === 'string' ? strToU8(value) : value])), options)
const sha = bytes => createHash('sha256').update(bytes).digest('hex')
// Fixture mutations exercise malformed container metadata. Production ZIP
// parsing remains entirely in zip.js.
function mutate(bytes, fn) {
  const copy = bytes.slice(), view = new DataView(copy.buffer)
  for (let i = 0; i <= copy.length - 4; i++) {
    const signature = view.getUint32(i, true)
    if (signature === 0x04034b50 || signature === 0x02014b50) fn(view, i, signature === 0x02014b50)
  }
  return copy
}

describe('bounded ZIP text inspection and selection', () => {
  it('reads DEFLATE without fetching codecs or creating nested workers', async () => {
    const fetch = vi.fn(() => { throw new Error('Unexpected network access') })
    const worker = vi.fn(() => { throw new Error('Unexpected nested worker') })
    vi.stubGlobal('fetch', fetch); vi.stubGlobal('Worker', worker)
    try {
      const result = await processZipArchive(zip({ 'a.txt': 'hello'.repeat(1000) }), 'a.zip', ['a.txt'])
      expect(result.files[0].text).toBe('hello'.repeat(1000))
      expect(fetch).not.toHaveBeenCalled(); expect(worker).not.toHaveBeenCalled()
    } finally { vi.unstubAllGlobals() }
  })
  it('hashes correctly on LAN HTTP without SubtleCrypto', async () => {
    vi.stubGlobal('crypto', undefined)
    try {
      const bytes = zip({ 'a.txt': 'hello' })
      const result = await processZipArchive(bytes, 'a.zip', ['a.txt'])
      expect(result.sha256).toBe(sha(bytes)); expect(result.files[0].sha256).toBe(sha(strToU8('hello')))
    } finally { vi.unstubAllGlobals() }
  })
  it('rejects archive-only ZIP64 footers even with ordinary 32-bit members', async () => {
    const original = Buffer.from(zip({ 'a.txt': 'hello' }, { level: 0 })), end = original.length - 22
    const eocd = Buffer.from(original.subarray(end)), footer = Buffer.alloc(56), locator = Buffer.alloc(20)
    footer.writeUInt32LE(0x06064b50); footer.writeBigUInt64LE(44n, 4)
    footer.writeUInt16LE(45, 12); footer.writeUInt16LE(45, 14)
    footer.writeBigUInt64LE(BigInt(eocd.readUInt16LE(8)), 24); footer.writeBigUInt64LE(BigInt(eocd.readUInt16LE(10)), 32)
    footer.writeBigUInt64LE(BigInt(eocd.readUInt32LE(12)), 40); footer.writeBigUInt64LE(BigInt(eocd.readUInt32LE(16)), 48)
    locator.writeUInt32LE(0x07064b50); locator.writeBigUInt64LE(BigInt(end), 8); locator.writeUInt32LE(1, 16)
    eocd.writeUInt16LE(0xffff, 8); eocd.writeUInt16LE(0xffff, 10); eocd.writeUInt32LE(0xffffffff, 12); eocd.writeUInt32LE(0xffffffff, 16)
    const bytes = new Uint8Array(Buffer.concat([original.subarray(0, end), footer, locator, eocd]))
    await expect(processZipArchive(bytes, 'a.zip', ['a.txt'])).rejects.toThrow('ZIP64')
  })
  it('accepts legal archive comments containing ZIP64 signature bytes', async () => {
    const bytes = zip({ 'a.txt': 'hello' }, { comment: 'comment PK\x06\x06 and PK\x06\x07 and PK\x05\x06' })
    expect((await processZipArchive(bytes, 'a.zip', ['a.txt'])).files[0].text).toBe('hello')
  })
  it.each([0, 6])('lists without reading, then returns exact selected text with hashes (level %s)', async level => {
    const text = '\ufeffOlá 世界\r\nline two\n', bytes = zip({ 'src/readme.md': text, 'other.txt': 'not selected', 'image.png': new Uint8Array([137, 0, 255]), 'empty/': new Uint8Array() }, { level })
    const listing = await processZipArchive(bytes, 'project.zip')
    expect(listing.sha256).toBe(sha(bytes)); expect(listing.files).toBeUndefined()
    expect(listing.entries.map(e => e.kind)).toEqual(['text', 'text', 'unread', 'directory'])
    expect(JSON.stringify(listing)).not.toContain('line two')
    const extracted = await processZipArchive(bytes, 'project.zip', ['src/readme.md'])
    expect(extracted.files).toEqual([{ path: 'src/readme.md', text, bytes: strToU8(text).length, sha256: sha(strToU8(text)) }])
  })
  it('does not inflate unread binaries even when their CRC is invalid', async () => {
    const bytes = mutate(zip({ 'asset.bin': new Uint8Array(1024) }), (v, i, central) => v.setUint32(i + (central ? 16 : 14), 123, true))
    expect((await processZipArchive(bytes, 'a.zip')).entries[0].kind).toBe('unread')
    await expect(processZipArchive(bytes, 'a.zip', ['asset.bin'])).rejects.toThrow('Only listed text')
  })
  it('checks selected CRC before returning any text', async () => {
    const bytes = mutate(zip({ 'a.txt': 'hello' }), (v, i, central) => v.setUint32(i + (central ? 16 : 14), 123, true))
    await expect(processZipArchive(bytes, 'a.zip', ['a.txt'])).rejects.toThrow(/CRC/i)
  })
  it.each(['../secret.txt', '/root.txt', 'C:secret.txt', 'a\\b.txt', 'a/./b.txt', 'a//b.txt', 'a\u0000b.txt'])('rejects unsafe path %s', async path => {
    await expect(processZipArchive(zip({ [path]: 'hello' }), 'a.zip')).rejects.toThrow(/path|filename/i)
  })
  it.each([{ 'A.txt': 'x', 'a.txt': 'y' }, { 'é.txt': 'x', 'e\u0301.txt': 'y' }, { 'a': 'x', 'a/b.txt': 'y' }])('rejects ambiguous names', async files => {
    await expect(processZipArchive(zip(files), 'a.zip')).rejects.toThrow(/ambiguous|conflicting/)
  })
  it('rejects links and special unix members', async () => {
    for (const mode of [0xa000, 0x1000]) {
      const bytes = mutate(zip({ 'link.txt': 'target' }), (v, i, central) => { if (central) { v.setUint16(i + 4, 0x314, true); v.setUint32(i + 38, mode << 16, true) } })
      await expect(processZipArchive(bytes, 'a.zip')).rejects.toThrow(/links|special/)
    }
  })
  it('rejects encrypted and unsupported compression entries', async () => {
    const original = zip({ 'a.txt': 'hello' })
    const encrypted = mutate(original, (v, i, central) => v.setUint16(i + (central ? 8 : 6), 1, true))
    await expect(processZipArchive(encrypted, 'a.zip')).rejects.toThrow(/Encrypted/)
    const method = mutate(original, (v, i, central) => v.setUint16(i + (central ? 10 : 8), 12, true))
    await expect(processZipArchive(method, 'a.zip')).rejects.toThrow(/stored|DEFLATE/)
  })
  it('rejects differing local and central headers and truncated archives', async () => {
    const original = zip({ 'a.txt': 'hello' })
    const mismatch = mutate(original, (v, i, central) => { if (!central) v.setUint32(i + 22, 3, true) })
    await expect(processZipArchive(mismatch, 'a.zip')).rejects.toThrow()
    await expect(processZipArchive(original.slice(0, -12), 'a.zip')).rejects.toThrow()
  })
  it('rejects excess archive bytes and member count', async () => {
    await expect(processZipArchive(new Uint8Array(ZIP_LIMITS.compressedBytes + 1), 'a.zip')).rejects.toThrow('4 MB')
    const files = Object.fromEntries(Array.from({ length: 65 }, (_, i) => [`${i}.txt`, 'x']))
    await expect(processZipArchive(zip(files), 'a.zip')).rejects.toThrow('64 entries')
  })
  it('enforces actual inflate bytes when both headers lie about expanded size', async () => {
    const bytes = mutate(zip({ 'bomb.txt': 'x'.repeat(2 * 1024 * 1024) }), (v, i, central) => v.setUint32(i + (central ? 24 : 22), 1, true))
    expect((await processZipArchive(bytes, 'a.zip')).entries[0].bytes).toBe(1)
    await expect(processZipArchive(bytes, 'a.zip', ['bomb.txt'])).rejects.toThrow(/expanded|size|compressed/i)
  })
  it('bounds aggregate selected text and marks oversized text unread', async () => {
    const files = Object.fromEntries(Array.from({ length: 5 }, (_, i) => [`${i}.txt`, 'x'.repeat(ZIP_LIMITS.memberBytes)]))
    await expect(processZipArchive(zip(files), 'a.zip', Object.keys(files))).rejects.toThrow('1 MB')
    const result = await processZipArchive(zip({ 'big.txt': 'x'.repeat(ZIP_LIMITS.memberBytes + 1) }), 'a.zip')
    expect(result.entries[0].kind).toBe('unread')
  })
  it.each([new Uint8Array([255]), new Uint8Array([65, 0, 66])])('rejects non UTF8 or binary text', async data => {
    await expect(processZipArchive(zip({ 'a.txt': data }), 'a.zip', ['a.txt'])).rejects.toThrow(/UTF-8|binary/)
  })
  it.each([[[]], [['a.txt', 'a.txt']], [['missing.txt']], [['a.bin']]])('rejects invalid selection %s', async selection => {
    await expect(processZipArchive(zip({ 'a.txt': 'hello', 'a.bin': 'x' }), 'a.zip', selection)).rejects.toThrow()
  })
})
