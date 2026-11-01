import { describe, expect, it } from 'vitest'
import { runtimeUnavailable } from './runtime'

describe('runtimeUnavailable', () => {
  it('reads the native runtime feature list from /api/status', () => {
    const status = { runtime: { kind: 'native', unavailable: ['extensions', 'pixel', 'talk'] } }
    expect(runtimeUnavailable(status, 'pixel')).toBe(true)
    expect(runtimeUnavailable(status, 'models')).toBe(false)
  })

  it('offers everything for Docker installs and older APIs', () => {
    expect(runtimeUnavailable({ runtime: { kind: 'docker', unavailable: [] } }, 'pixel')).toBe(false)
    expect(runtimeUnavailable({}, 'pixel')).toBe(false)
    expect(runtimeUnavailable(null, 'talk')).toBe(false)
  })
})
