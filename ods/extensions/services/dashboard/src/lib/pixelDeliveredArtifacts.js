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

// Model-authored hosts never authorize a download. A canonical snapshot/file
// path may select exactly one verified receipt from this completed response;
// the download control still fetches and checks the ODS snapshot's bytes.
function authoredArtifactPath(href) {
  if (typeof href !== 'string' || !/^https?:\/\//i.test(href)
    || /[\s\\]/.test(href)) return null
  let url
  try { url = new URL(href) } catch { return null }
  if (url.username || url.password || url.search || url.hash) return null
  const slash = href.indexOf('/', href.indexOf('://') + 3)
  const rawPath = slash < 0 ? '' : href.slice(slash)
  // URL parsing normalizes dot segments and backslashes. Never let that turn
  // an unrelated or traversal-shaped URL into a receipt match.
  if (rawPath !== url.pathname) return null
  return rawPath
}

export function deliveredArtifactForHref(href, artifacts) {
  const delivered = parseDeliveredArtifacts(artifacts)
  const rawPath = authoredArtifactPath(href)
  if (!delivered?.length || !rawPath) return null
  const matches = delivered.filter(({siteId, file}) => [
    `/${siteId}/${file.path}`,
    `/${siteId.slice(5)}/${file.path}`,
    `/pixel-preview/${siteId}/${file.path}`,
    `/preview/${siteId}/${file.path}`,
  ].includes(rawPath))
  return matches.length === 1 ? matches[0] : null
}

// Called only for an explicit Download-labelled link in ordinary answer prose.
// The filename can select one completed receipt; the URL never supplies a host,
// snapshot ID or bytes. Ambiguous names and URL paths requiring normalization
// cannot bind.
export function deliveredArtifactForDownloadHref(href, artifacts) {
  const delivered = parseDeliveredArtifacts(artifacts)
  const rawPath = authoredArtifactPath(href)
  if (!delivered?.length || !rawPath) return null
  const name = rawPath.split('/').at(-1)
  const matches = delivered.filter(({file}) => file.path === name)
  return matches.length === 1 ? matches[0] : null
}

// An explicit Download filename in ordinary prose may select one completed
// receipt. The authored URL is not consulted or fetched; the receipt remains
// the sole authority for the snapshot, filename, digest and bytes.
export function deliveredArtifactForDownloadName(name, artifacts) {
  const delivered = parseDeliveredArtifacts(artifacts)
  if (!delivered?.length || typeof name !== 'string') return null
  const matches = delivered.filter(({file}) => file.path === name)
  return matches.length === 1 ? matches[0] : null
}

const EMBED_DIRECTIVE = /^\[embed ref="artifact_([a-f0-9]{64})" title="([^"]+)" height="([0-9]+)" \/\](?:\r?\n)*$/

// Returns '' only when the entire assistant content is exactly one stand-alone
// embed directive whose ref matches a delivered artifact's file.sha256 and
// whose title matches that artifact's file.path. Any surrounding prose,
// indentation, quoting, fencing, or mismatch preserves the original content.
export function deliveredArtifactDisplayText(content, artifacts) {
  if (typeof content !== 'string') return content
  const match = EMBED_DIRECTIVE.exec(content)
  if (!match) return content
  const [, ref, title] = match
  const delivered = parseDeliveredArtifacts(artifacts)
  if (!delivered?.length) return content
  for (const artifact of delivered) {
    if (artifact?.file?.sha256 === ref && artifact?.file?.path === title) return ''
  }
  return content
}
