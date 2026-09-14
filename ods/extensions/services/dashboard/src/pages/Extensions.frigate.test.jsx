import { afterEach, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { render } from '../test/test-utils'
import Extensions from './Extensions'
import catalog from '../../../../../config/extensions-catalog.json'

afterEach(() => vi.unstubAllGlobals())

it('opens the shipped Frigate catalog entry on its authenticated HTTPS listener', async () => {
  const frigate = catalog.extensions.find(entry => entry.id === 'frigate')
  vi.stubGlobal('fetch', vi.fn(async (url) => {
    if (String(url).includes('/api/extensions/catalog')) {
      return { ok: true, json: async () => ({
        agent_available: true,
        extensions: [{ ...frigate, status: 'enabled', source: 'user' }],
        summary: { total: 1, installed: 1 },
      }) }
    }
    if (String(url).includes('/api/templates')) return { ok: true, json: async () => ({ templates: [] }) }
    throw new Error(`Unexpected fetch: ${url}`)
  }))
  render(<Extensions compact />)
  expect(await screen.findByRole('link', { name: /8971/ })).toHaveAttribute('href', 'https://localhost:8971')
})
