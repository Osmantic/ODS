import { act, cleanup, fireEvent, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { render } from '../test/test-utils'
import Extensions from './Extensions' // eslint-disable-line no-unused-vars

const response = data => ({ ok: true, json: async () => data })

async function details(extension) {
  vi.stubGlobal('fetch', vi.fn(async url => {
    if (url === '/api/extensions/catalog') return response({
      agent_available: true, summary: { total: 1 },
      extensions: [{ status: 'enabled', features: [], ...extension }],
    })
    if (url === '/api/templates') return response({ templates: [] })
    if (url === '/api/webui/selection') return response({ supported: true, enabled: true })
    throw new Error(`Unexpected request: ${url}`)
  }))
  await act(async () => { render(<Extensions compact />) })
  fireEvent.click(screen.getByRole('button', { name: `Details for ${extension.name}` }))
  return within(screen.getByRole('dialog', { name: extension.name }))
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it.each([
  { id: 'open-webui', name: 'Open WebUI', source: 'core', category: 'core' },
  { id: 'dashboard', name: 'Dashboard', source: 'core', category: 'core' },
  { id: 'custom', name: 'Custom core service', source: 'user', category: 'core' },
])('omits CLI selection commands refused for $name', async extension => {
  const dialog = await details(extension)
  expect(dialog.getByText('enabled')).toBeVisible()
  expect(dialog.queryByText('CLI Commands')).toBeNull()
  expect(dialog.queryByText(`ods enable ${extension.id}`)).toBeNull()
  expect(dialog.queryByText(`ods disable ${extension.id}`)).toBeNull()
})

it.each([
  { id: 'n8n', name: 'n8n', source: 'core', category: 'automation', library_manageable: true },
  { id: 'optional', name: 'Optional extension', source: 'user', category: 'optional' },
  { id: 'legacy', name: 'Legacy extension', source: 'user' },
])('retains supported CLI selection commands for $name', async extension => {
  const dialog = await details(extension)
  expect(dialog.getByText(`ods enable ${extension.id}`)).toBeVisible()
  expect(dialog.getByText(`ods disable ${extension.id}`)).toBeVisible()
})
