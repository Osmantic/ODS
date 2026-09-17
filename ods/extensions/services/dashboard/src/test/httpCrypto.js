import {vi} from 'vitest'

// HTTP LAN origins expose getRandomValues, but not secure-context randomUUID.
export function mockHttpCrypto(uuid) {
  const seed = Uint8Array.from(uuid.replaceAll('-', '').match(/../g), byte => Number.parseInt(byte,16))
  vi.stubGlobal('crypto', {getRandomValues:vi.fn(bytes => {bytes.set(seed); return bytes})})
}
