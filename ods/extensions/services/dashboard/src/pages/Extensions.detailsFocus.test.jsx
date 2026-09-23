import { expect, it, vi, afterEach } from 'vitest'
import { fireEvent, screen } from '@testing-library/react'
import { render } from '../test/test-utils'
import Extensions from './Extensions'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('returns focus to the details trigger after closing extension details', async () => {
  vi.stubGlobal('fetch', vi.fn(async url => {
    if (String(url).includes('/api/extensions/catalog')) return {
      ok: true, status: 200,
      json: async () => ({ agent_available: true, gpu_backend: 'cpu', extensions: [{
        id: 'demo', name: 'Demo extension', status: 'not_installed', source: 'user',
        description: 'Demo', features: [{ category: 'tools', icon: 'Box' }],
      }], summary: { total: 1, not_installed: 1 },
    })
    }
    if (String(url).includes('/api/templates')) return { ok: true, status: 200, json: async () => ({ templates: [] }) }
    throw new Error(`Unmocked fetch: ${url}`)
  }))
  render(<Extensions />)
  const trigger = await screen.findByRole('button', { name: 'Details for Demo extension' })
  fireEvent.click(trigger)
  fireEvent.click(screen.getByRole('button', { name: 'Close extension details' }))
  expect(document.activeElement).toBe(trigger)
})
