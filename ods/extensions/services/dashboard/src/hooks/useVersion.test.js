import { afterEach, describe, expect, it, vi } from 'vitest'
import { triggerUpdate } from './useVersion'

afterEach(() => vi.unstubAllGlobals())

describe('triggerUpdate', () => {
  it('preserves plain-text server errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('maintenance window', { status: 503 })))
    await expect(triggerUpdate('restart')).rejects.toThrow('maintenance window')
  })

  it('uses structured error details when the response is JSON', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'Update rejected' }), { status: 409 })))
    await expect(triggerUpdate('restart')).rejects.toThrow('Update rejected')
  })
})
