import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  RECOVERY_KEY,
  RECOVERY_WINDOW_MS,
  clearStaleAssetRecovery,
  isStaleAssetError,
  recoverFromStaleAsset,
} from '../staleAssetRecovery'

describe('stale asset recovery', () => {
  beforeEach(() => {
    globalThis.sessionStorage.clear()
  })

  it.each([
    new TypeError('Failed to fetch dynamically imported module: /assets/Models-old.js'),
    Object.assign(new Error('Loading chunk 42 failed'), { name: 'ChunkLoadError' }),
    new Error('Importing a module script failed.'),
    new Error('Unable to preload CSS for /assets/app-old.css'),
  ])('recognizes browser and bundler stale-asset failures', (error) => {
    expect(isStaleAssetError(error)).toBe(true)
  })

  it('does not disguise an unrelated application crash', () => {
    expect(isStaleAssetError(new TypeError('Cannot read properties of undefined'))).toBe(false)
  })

  it('reloads once and records the attempt', () => {
    const reload = vi.fn()
    const recovered = recoverFromStaleAsset(
      new TypeError('Failed to fetch dynamically imported module: /assets/Models-old.js'),
      { reload, href: 'http://localhost/models', now: 1_000 },
    )

    expect(recovered).toBe(true)
    expect(reload).toHaveBeenCalledOnce()
    expect(JSON.parse(globalThis.sessionStorage.getItem(RECOVERY_KEY))).toEqual({
      at: 1_000,
      href: 'http://localhost/models',
    })
  })

  it('blocks a reload loop while the recovery marker is recent', () => {
    globalThis.sessionStorage.setItem(RECOVERY_KEY, JSON.stringify({ at: 1_000 }))
    const reload = vi.fn()

    expect(recoverFromStaleAsset(new Error('Loading chunk 9 failed'), {
      reload,
      now: 1_000 + RECOVERY_WINDOW_MS - 1,
    })).toBe(false)
    expect(reload).not.toHaveBeenCalled()
  })

  it('allows a later deployment to recover after the window', () => {
    globalThis.sessionStorage.setItem(RECOVERY_KEY, JSON.stringify({ at: 1_000 }))
    const reload = vi.fn()

    expect(recoverFromStaleAsset(new Error('Loading chunk 9 failed'), {
      reload,
      now: 1_000 + RECOVERY_WINDOW_MS,
    })).toBe(true)
    expect(reload).toHaveBeenCalledOnce()
  })

  it('does not reload when loop protection cannot be persisted', () => {
    const storage = { getItem: vi.fn(() => null), setItem: vi.fn(() => { throw new Error('blocked') }) }
    const reload = vi.fn()

    expect(recoverFromStaleAsset(new Error('Loading chunk 9 failed'), { storage, reload })).toBe(false)
    expect(reload).not.toHaveBeenCalled()
  })

  it('clears a completed recovery marker', () => {
    globalThis.sessionStorage.setItem(RECOVERY_KEY, '{}')
    clearStaleAssetRecovery()
    expect(globalThis.sessionStorage.getItem(RECOVERY_KEY)).toBeNull()
  })
})
