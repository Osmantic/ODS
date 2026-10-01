import { isArtifactPath, isSnapshotId } from './pixelArtifacts'

const DIGEST = /^[a-f0-9]{64}$/
const isDigest = value => typeof value === 'string' && DIGEST.test(value)
const FORMATS = /\.(?:md|markdown|txt|csv|tsv|json|pdf|zip|rar|docx|xlsx|pptx)$/i
const exactKeys = (value, keys) => value !== null && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).sort().join(',') === keys.slice().sort().join(',')

// Accept only bounded host receipts. Conversation text, including MEDIA:, is
// never converted to a download URL or used to select a workspace file.
export function parseDeliveredArtifacts(value) {
  if (!Array.isArray(value) || value.length > 4) return null
  const seen = new Set()
  const artifacts = []
  for (const item of value) {
    if (!exactKeys(item, ['schemaVersion', 'kind', 'relativePath', 'siteId', 'sha256', 'file'])
      || item.schemaVersion !== 1 || item.kind !== 'ods-pixel-workspace-artifact'
      || !isArtifactPath(item.relativePath) || item.relativePath.length > 512 || item.relativePath.split('/').length > 12
      || !isSnapshotId(item.siteId) || !isDigest(item.sha256)
      || item.siteId !== `site-${item.sha256.slice(0, 24)}`
      || !exactKeys(item.file, ['path', 'bytes', 'sha256'])
      || item.file.path !== item.relativePath.split('/').at(-1)
      || !FORMATS.test(item.file.path) || !isDigest(item.file.sha256)
      || !Number.isSafeInteger(item.file.bytes) || item.file.bytes < 0 || item.file.bytes > 4 * 1024 * 1024
      || seen.has(`${item.siteId}/${item.file.path}`)) return null
    seen.add(`${item.siteId}/${item.file.path}`)
    artifacts.push({ ...item, file: { ...item.file } })
  }
  return artifacts
}

export function parseDeliveredArtifactsFrame(frame) {
  const marker = frame?.pixel_artifacts
  if (frame?.error || frame?.choices?.[0]?.finish_reason !== 'stop'
    || !exactKeys(marker, ['schemaVersion', 'artifacts']) || marker.schemaVersion !== 1) return null
  return parseDeliveredArtifacts(marker.artifacts)
}

export function deliveredArtifactMetadata(message) {
  if (message?.role !== 'assistant' || message.status !== 'done') return {}
  const artifacts = parseDeliveredArtifacts(message.artifacts)
  return artifacts?.length ? { artifacts } : {}
}
