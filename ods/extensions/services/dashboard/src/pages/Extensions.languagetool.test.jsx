import { afterEach, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { render } from '../test/test-utils'
import Extensions from './Extensions'
import catalog from '../../../../../config/extensions-catalog.json'

afterEach(() => vi.unstubAllGlobals())

it('identifies the installed LanguageTool recipe as an API service', async () => {
  const entry = catalog.extensions.find(item => item.id === 'languagetool')
  vi.stubGlobal('fetch', vi.fn(async (url) => {
    if (String(url).includes('/api/extensions/catalog')) return {
      ok: true, json: async () => ({
        agent_available: true,
        extensions: [{ ...entry, status: 'enabled', source: 'user' }],
        summary: { total: 1, installed: 1 },
      }),
    }
    if (String(url).includes('/api/templates')) return { ok: true, json: async () => ({ templates: [] }) }
    throw new Error(`Unexpected fetch: ${url}`)
  }))
  render(<Extensions compact />)
  expect(await screen.findByText('LanguageTool')).toBeVisible()
  expect(screen.getByText('API service')).toBeVisible()
  expect(screen.queryByRole('link', { name: /7826/ })).toBeNull()
})
