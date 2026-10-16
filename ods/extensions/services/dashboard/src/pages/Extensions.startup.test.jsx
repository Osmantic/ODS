import { act, cleanup, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { render } from '../test/test-utils'
import Extensions from './Extensions' // eslint-disable-line no-unused-vars

const response = data => ({ ok: true, json: async () => data })
const startingLabel = 'Starting service — waiting for health check...'

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

it.each(['idle', 'started'])('tracks observed startup after reload with %s progress, without replaying Enable', async progress => {
  vi.useFakeTimers()
  let state = { status: 'installing', runtime_starting: true }
  const fetch = vi.fn(async url => {
    if (url === '/api/extensions/catalog') return response({
      agent_available: true, summary: { total: 1 }, extensions: [{
        id: 'n8n', name: 'n8n', source: 'core', library_manageable: true,
        library_selected: true, features: [], ...state,
      }],
    })
    if (url === '/api/templates') return response({ templates: [] })
    if (url === '/api/webui/selection') return response({ supported: false, enabled: true })
    if (url === '/api/extensions/n8n/progress') return response({ status: progress, phase_label: 'Service started' })
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal('fetch', fetch)
  let view
  await act(async () => { view = render(<Extensions compact />) })
  expect(screen.getByText(startingLabel)).toBeVisible()
  expect(screen.queryByRole('button', { name: /Retry/ })).toBeNull()
  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
  expect(screen.getByText(startingLabel)).toBeVisible()
  expect(screen.queryByText('Service started')).toBeNull()

  view.unmount()
  await act(async () => { render(<Extensions compact />) })
  expect(screen.getByText(startingLabel)).toBeVisible()
  state = { status: 'unhealthy', runtime_starting: false }
  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
  expect(screen.queryByText(startingLabel)).toBeNull()
  expect(screen.getByRole('button', { name: 'Retry n8n' })).toBeEnabled()

  state = { status: 'enabled', runtime_starting: false }
  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
  expect(screen.queryByText(startingLabel)).toBeNull()
  expect(screen.queryByRole('button', { name: /Retry/ })).toBeNull()
  expect(screen.getByRole('button', { name: 'Disable n8n' })).toBeEnabled()
  expect(fetch.mock.calls.every(([, options]) => !options?.method || options.method === 'GET')).toBe(true)
})
