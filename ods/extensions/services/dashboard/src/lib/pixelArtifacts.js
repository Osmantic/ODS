const DIGEST = /^[a-f0-9]{64}$/
export const isSnapshotId = value => typeof value === 'string' && /^site-[a-f0-9]{24}$/.test(value)
export const isArtifactPath = value => typeof value === 'string' && value.length <= 1664
  && value.split('/').length <= 13
  && value.split('/').every(part => /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(part))

export async function readBoundedBytes(response, maximum) {
  if (!response.ok || Number(response.headers?.get('Content-Length')) > maximum) throw new Error('Unavailable artifact')
  if (!response.body?.getReader) {
    const data = await response.arrayBuffer()
    if (data.byteLength > maximum) throw new Error('Oversized artifact')
    return data
  }
  const reader = response.body.getReader()
  const chunks = []
  let size = 0
  try {
    while (true) {
      const {value, done} = await reader.read()
      if (done) break
      size += value.byteLength
      if (size > maximum) { await reader.cancel(); throw new Error('Oversized artifact') }
      chunks.push(value)
    }
  } finally { reader.releaseLock() }
  const bytes = new Uint8Array(size)
  let offset = 0
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength }
  return bytes.buffer
}

export async function sha256(bytes) {
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), byte => byte.toString(16).padStart(2, '0')).join('')
}

export async function loadSnapshotFiles(preview, signal) {
  if (!isSnapshotId(preview.siteId) || !DIGEST.test(preview.sha256)) throw new Error('Invalid snapshot')
  const response = await fetch(`/pixel-preview/${preview.siteId}/__ods_manifest__.json`, {signal, cache:'no-store'})
  const bytes = await readBoundedBytes(response, 256 * 1024)
  const manifest = JSON.parse(new TextDecoder('utf-8', {fatal:true}).decode(bytes))
  if (manifest.schemaVersion !== 1 || manifest.siteId !== preview.siteId
    || manifest.sha256 !== preview.sha256 || manifest.bytes !== preview.bytes
    || !Array.isArray(manifest.files) || manifest.files.length !== preview.files
    || manifest.files.length < 1 || manifest.files.length > 128) throw new Error('Snapshot mismatch')
  let total = 0
  const seen = new Set()
  for (const file of manifest.files) {
    if (!file || !isArtifactPath(file.path) || seen.has(file.path) || !DIGEST.test(file.sha256)
      || !Number.isInteger(file.bytes) || file.bytes < 1 || file.bytes > 4 * 1024 * 1024) throw new Error('Invalid file')
    total += file.bytes
    seen.add(file.path)
  }
  if (total !== preview.bytes || total > 16 * 1024 * 1024
    || manifest.files.find(file => file.path === 'index.html')?.sha256 !== preview.entrySha256) throw new Error('Invalid entry')
  return manifest.files.map(({path, bytes, sha256}) => ({path, bytes, sha256}))
}

export async function loadArtifactBytes(preview, file, signal) {
  if (!isSnapshotId(preview.siteId) || !isArtifactPath(file.path) || !DIGEST.test(file.sha256)) throw new Error('Invalid file')
  const tail = file.path === 'index.html' ? '' : file.path.split('/').map(encodeURIComponent).join('/')
  const response = await fetch(`/pixel-preview/${preview.siteId}/${tail}`, {signal, cache:'no-store'})
  const bytes = await readBoundedBytes(response, 4 * 1024 * 1024)
  if (file.bytes != null && file.bytes !== bytes.byteLength) throw new Error('File length mismatch')
  if (await sha256(bytes) !== file.sha256) throw new Error('File digest mismatch')
  return bytes
}
