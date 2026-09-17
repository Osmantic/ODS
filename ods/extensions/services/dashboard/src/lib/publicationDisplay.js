import {isSnapshotId} from './pixelArtifacts'

const READY = 'Your preview is ready.'
const SCOPE = 'Publication scope: this receipt verifies the published snapshot, not functional behavior or completion of other requested work.'
const escapePattern = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

function insideFence(text) {
  let fence = null
  for (const line of text.split(/\r?\n/)) {
    const marker = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line)
    if (!marker) continue
    if (!fence) {
      if (marker[1][0] !== '`' || !marker[2].includes('`')) fence = marker[1]
    } else if (marker[1][0] === fence[0] && marker[1].length >= fence.length && !marker[2].trim()) fence = null
  }
  return fence !== null
}

// Presentation only. Call with the message's validated publication metadata,
// alongside its publication card; retain the original receipt in stored chat
// and model history. A link by itself never establishes a publication.
export function publicationDisplayText(content, publication) {
  if (typeof content !== 'string' || publication?.schemaVersion !== 1
    || publication.kind !== 'ods-pixel-workspace-preview' || !isSnapshotId(publication.siteId)
    || !/^[a-f0-9]{64}$/.test(publication.sha256)
    || publication.siteId !== `site-${publication.sha256.slice(0, 24)}`
    || !Number.isInteger(publication.port) || publication.port < 1 || publication.port > 65535
    || publication.url !== `http://${publication.siteId}.localhost:${publication.port}/${publication.siteId}/`) return content

  const relay = `/pixel-preview/${publication.siteId}/`
  const urls = [publication.url, relay]
  // A transcript copied from this dashboard may already use its relay URL.
  // Match only the current origin, never an arbitrary URL with a similar path.
  if (/^https?:\/\//.test(globalThis.location?.origin || '')) urls.push(`${globalThis.location.origin}${relay}`)
  const newline = '\\r?\\n'
  const paragraph = `${newline}(?:[ \\t]*${newline})+`
  const link = `\\[Open preview\\]\\((?:${urls.map(escapePattern).join('|')})\\)`
  const receipt = new RegExp(`(?:^|${paragraph})(?:${escapePattern(READY)}${paragraph})?${link}(?:${paragraph}(?:Created by Pixel\\.|Published from your workspace\\.))?(?:${newline}${escapePattern(SCOPE)})?[ \\t\\r\\n]*$`)
  let display = content
  while (true) {
    const match = receipt.exec(display)
    if (!match || insideFence(display.slice(0, match.index))) return display
    display = display.slice(0, match.index)
  }
}
