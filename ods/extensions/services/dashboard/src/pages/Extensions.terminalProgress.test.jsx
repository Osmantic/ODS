import { act, cleanup, fireEvent, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { render } from '../test/test-utils'
import Extensions from './Extensions' // eslint-disable-line no-unused-vars

const response = data => ({ ok: true, json: async () => data })
const service = overrides => ({
  id: 'perplexica', name: 'Perplexica', source: 'core',
  status: 'error', library_manageable: true, library_selected: true,
  features: [], ...overrides,
})

async function show(ext, progress) {
  vi.useFakeTimers()
  vi.stubGlobal('fetch', vi.fn(async url => {
    if (url === '/api/extensions/catalog') return response({
      agent_available: true, extensions: [ext], summary: { total: 1 },
    })
    if (url === '/api/templates') return response({ templates: [] })
    if (url === '/api/webui/selection') return response({ supported: false, enabled: true })
    if (url === `/api/extensions/${ext.id}/progress`) return response(progress)
    throw new Error(`Unexpected request: ${url}`)
  }))
  await act(async () => { render(<Extensions compact />) })
}

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

it.each(['', 'Downloading image...'])('shows a failed service without install progress or duplicate retries (phase %s)', async phase => {
  await show(service(), { status: 'error', phase_label: phase, error: 'Host agent failed to start extension.' })
  expect(screen.getByText('Host agent failed to start extension.')).toBeVisible()
  expect(screen.queryByText('Installing...')).toBeNull()
  expect(screen.queryByText('Downloading image...')).toBeNull()
  expect(screen.getAllByRole('button', { name: /Retry/ })).toHaveLength(1)
  fireEvent.click(screen.getByRole('button', { name: 'Retry Perplexica' }))
  expect(screen.getByRole('dialog', { name: 'Confirm action' })).toHaveTextContent('Enable Perplexica?')
})

it.each([
  ['installing', 'Installing...'], ['setting_up', 'Running setup...'],
])('shows %s progress before the first progress response', async (status, label) => {
  await show(service({ status }), { status: 'idle' })
  expect(screen.getByText(label)).toBeVisible()
})

it('offers only the managed retry when no error progress record is available', async () => {
  await show(service(), { status: 'idle' })
  expect(screen.getAllByRole('button', { name: /Retry/ })).toHaveLength(1)
  expect(screen.getByRole('button', { name: 'Retry Perplexica' })).toBeEnabled()
})

it.each([
  ['pulling', 'Downloading image...'],
  ['starting', 'Starting container...'],
  ['setup_hook', 'Running setup...'],
])('preserves active %s progress', async (status, label) => {
  await show(service({ status: 'installing' }), { status, phase_label: label })
  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
  expect(screen.getByText(label)).toBeVisible()
})

it('uses the setup fallback when active setup has no phase label', async () => {
  await show(service({ status: 'installing' }), { status: 'setup_hook' })
  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
  expect(screen.getByText('Running setup...')).toBeVisible()
})

it('does not keep showing an install spinner when progress has completed ahead of catalog refresh', async () => {
  await show(service({ status: 'installing' }), { status: 'started', phase_label: 'Service started' })
  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
  expect(screen.queryByText('Installing...')).toBeNull()
  expect(screen.queryByText('Service started')).toBeNull()
})

it.each([
  [service({ id: 'user-tool', name: 'User tool', source: 'user', library_manageable: false }), 'Enable User tool?'],
  [service({ id: 'opencode', name: 'OpenCode', library_manageable: false, installable: true, app_path: '/apps/opencode' }), 'Install OpenCode?'],
])('keeps one retry with the correct action for $name', async (ext, dialogText) => {
  await show(ext, { status: 'error', error: 'Setup failed' })
  expect(screen.getAllByRole('button', { name: /Retry/ })).toHaveLength(1)
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  expect(screen.getByRole('dialog', { name: 'Confirm action' })).toHaveTextContent(dialogText)
})
