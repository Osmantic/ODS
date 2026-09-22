import { afterEach, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { render } from '../test/test-utils'
import Extensions from './Extensions'
import catalog from '../../../../../config/extensions-catalog.json'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it('copies an install-environment lookup for Miniflux credentials renamed by Compose', async () => {
  const extension = catalog.extensions.find(item => item.id === 'miniflux')
  const writeText = vi.fn().mockResolvedValue(undefined)
  vi.stubGlobal('navigator', { clipboard: { writeText } })
  vi.stubGlobal('fetch', vi.fn(async url => ({
    ok: true,
    json: async () => String(url).includes('/api/templates')
      ? { templates: [] }
      : { extensions: [{ ...extension, source: 'user', status: 'enabled' }], agent_available: true },
  })))
  render(<Extensions compact />)
  fireEvent.click(await screen.findByRole('button', { name: `Details for ${extension.name}` }))

  expect(screen.getByText(/from your ODS installation directory/i)).toBeVisible()
  expect(screen.getByText(/password changed inside an application/i)).toBeVisible()
  expect(screen.queryByText(/^docker exec ods-miniflux env/)).toBeNull()
  const command = screen.getByText(/^grep -E /)
  fireEvent.click(within(command.parentElement).getByTitle('Copy to clipboard'))
  await waitFor(() => expect(writeText).toHaveBeenCalledOnce())
  const copied = writeText.mock.calls[0][0]
  expect(copied).toContain('MINIFLUX_DB_PASSWORD|MINIFLUX_ADMIN_PASSWORD')
  expect(copied).toContain('^[[:space:]]*(export[[:space:]]+)?(')
  expect(copied).toMatch(/\[\[:space:\]\]\*=' \.env$/)
  expect(copied).not.toContain('docker')
})

it('does not add credential instructions to an extension with only port settings', async () => {
  vi.stubGlobal('fetch', vi.fn(async url => ({
    ok: true,
    json: async () => String(url).includes('/api/templates') ? { templates: [] } : {
      extensions: [{ id: 'local-tool', name: 'Local Tool', status: 'enabled', source: 'user',
        env_vars: [{ key: 'LOCAL_TOOL_PORT' }], features: [] }], agent_available: true,
    },
  })))
  render(<Extensions compact />)
  fireEvent.click(await screen.findByRole('button', { name: 'Details for Local Tool' }))
  expect(screen.queryByText(/from your ODS installation directory/i)).toBeNull()
  expect(screen.queryByText(/^grep -E /)).toBeNull()
})

it('reports command-copy failures so the command can be copied manually', async () => {
  const extension = catalog.extensions.find(item => item.id === 'miniflux')
  vi.stubGlobal('navigator', { clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } })
  vi.stubGlobal('fetch', vi.fn(async url => ({
    ok: true,
    json: async () => String(url).includes('/api/templates')
      ? { templates: [] }
      : { extensions: [{ ...extension, source: 'user', status: 'enabled' }], agent_available: true },
  })))
  render(<Extensions compact />)
  fireEvent.click(await screen.findByRole('button', { name: `Details for ${extension.name}` }))

  fireEvent.click(within(screen.getByText(/^grep -E /).parentElement).getByRole('button', { name: 'Copy to clipboard' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Copy failed')
  expect(screen.getByRole('button', { name: 'Copy failed' })).toBeVisible()
})
