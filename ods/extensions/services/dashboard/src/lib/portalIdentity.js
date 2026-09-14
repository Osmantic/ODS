export const DEFAULT_ASSISTANT_NAME = 'Assistant'

export function normalizeDisplayName(value) {
  if (typeof value !== 'string' || value.length > 480 || [...value].length > 240) throw new Error('Invalid assistant name')
  let name = value.normalize('NFC')
  for (const character of name) {
    if (/[\p{Cc}\p{Cs}\p{Zl}\p{Zp}]/u.test(character)
      || (/\p{Cf}/u.test(character) && !['\u200c', '\u200d'].includes(character))) throw new Error('Invalid assistant name')
  }
  name = name.trim() || DEFAULT_ASSISTANT_NAME
  if ([...name].length > 60) throw new Error('Assistant name must be at most 60 characters')
  return name
}

export function readIdentity(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || Object.keys(value).length !== 3 || !['schemaVersion', 'revision', 'displayName'].every(key => Object.hasOwn(value, key))
    || value.schemaVersion !== 1 || !Number.isSafeInteger(value.revision) || value.revision < 0
    || normalizeDisplayName(value.displayName) !== value.displayName) throw new Error('Invalid assistant identity')
  return { schemaVersion: 1, revision: value.revision, displayName: value.displayName }
}

export async function readIdentityResponse(response) {
  if (!response.ok || !response.body?.getReader) throw new Error('Assistant identity request failed')
  const reader = response.body.getReader()
  const bytes = new Uint8Array(2048)
  let length = 0
  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      if (length + value.byteLength > bytes.length) throw new Error('Assistant identity response too large')
      bytes.set(value, length)
      length += value.byteLength
    }
    return readIdentity(JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes.subarray(0, length))))
  } finally {
    void reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}
