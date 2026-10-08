import { act, cleanup, fireEvent, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { render } from '../test/test-utils'
import Extensions from './Extensions' // eslint-disable-line no-unused-vars

/**
 * Bundled services download their images before they are enabled. A first
 * download can outlast any request on a slow link (Mac mini, 2026-10-04:
 * Hermes and Open WebUI failed at 600 s), so the card follows the download
 * through /progress and the enable starts only once the images are local.
 */

const response = (data, status = 200) => ({ ok: status < 400, status, json: async () => data })
const DOWNLOADING = 'Downloading images... 0:15 elapsed, 2 layers done'

async function show(ext, progress, { prepareStatus = 202, webuiSelection = { supported: false, enabled: true } } = {}) {
  vi.useFakeTimers()
  const calls = []
  const records = [...progress]
  const actionId = ext.id === 'hermes' ? 'hermes-proxy' : ext.id
  const actionQuery = ext.id === 'hermes' ? '?auto_enable_deps=true' : ''
  const extensions = ext.id === 'hermes'
    ? [ext, { ...ext, id: 'hermes-proxy', name: 'Hermes Auth Proxy' }] : [ext]
  vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
    const target = String(url)
    calls.push([target, options.method || 'GET'])
    if (target === '/api/extensions/catalog') {
      return response({ agent_available: true, extensions, summary: { total: extensions.length } })
    }
    if (target === '/api/templates') return response({ templates: [] })
    if (target === '/api/webui/selection' && options.method === 'POST') {
      webuiSelection.enabled = true
      return response({ enabled: true, action: 'enabled' })
    }
    if (target === '/api/webui/selection') return response(webuiSelection)
    if (target === `/api/extensions/${actionId}/prepare${actionQuery}`) {
      return response({ status: prepareStatus === 202 ? 'accepted' : 'ready', service_ids: extensions.map(e => e.id) }, prepareStatus)
    }
    if (target === `/api/extensions/${actionId}/progress`) {
      return response(records.length > 1 ? records.shift() : records[0])
    }
    if (target === `/api/extensions/${actionId}/enable${actionQuery}`) {
      extensions.forEach(entry => Object.assign(entry, { status: 'enabled', library_selected: true }))
      return response({ enabled_services: extensions.map(e => e.id), failed_services: [] })
    }
    throw new Error(`Unexpected request: ${target}`)
  }))
  await act(async () => { render(<Extensions compact />) })
  return calls
}

const hermes = () => ({
  id: 'hermes', name: 'Hermes Agent', source: 'core', status: 'disabled',
  library_manageable: true, library_selected: false, features: [],
})
const posted = (calls, path) => calls.some(([url, method]) => url === path && method === 'POST')

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

it('shows the image download on the card and enables only after it finishes', async () => {
  const calls = await show(hermes(), [
    { service_id: 'hermes-proxy', status: 'pulling', phase_label: DOWNLOADING },
    { service_id: 'hermes-proxy', status: 'prepared', phase_label: 'Images downloaded' },
  ])
  fireEvent.click(screen.getByRole('button', { name: 'Add Hermes with web access' }))
  fireEvent.click(screen.getByRole('button', { name: 'Enable' }))
  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })

  const agentCard = within(screen.getByRole('heading', { name: 'Hermes Agent' }).closest('article'))
  expect(agentCard.getByText(DOWNLOADING)).toBeVisible()
  expect(posted(calls, '/api/extensions/hermes-proxy/prepare?auto_enable_deps=true')).toBe(true)
  expect(posted(calls, '/api/extensions/hermes-proxy/enable?auto_enable_deps=true')).toBe(false)

  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
  expect(posted(calls, '/api/extensions/hermes-proxy/enable?auto_enable_deps=true')).toBe(true)
  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
  expect(screen.queryByText(DOWNLOADING)).toBeNull()
  expect(screen.getByText('Extension installed and started.')).toBeVisible()
})

it('enables at once when the images are already here', async () => {
  const calls = await show(hermes(), [{ service_id: 'hermes-proxy', status: 'idle' }], { prepareStatus: 200 })
  fireEvent.click(screen.getByRole('button', { name: 'Add Hermes with web access' }))
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Enable' })) })
  expect(posted(calls, '/api/extensions/hermes-proxy/enable?auto_enable_deps=true')).toBe(true)
})

it('reports a failed download and never enables', async () => {
  const calls = await show(hermes(), [
    { service_id: 'hermes-proxy', status: 'error', error: 'Image download made no progress for 15 minutes.' },
  ])
  fireEvent.click(screen.getByRole('button', { name: 'Add Hermes with web access' }))
  fireEvent.click(screen.getByRole('button', { name: 'Enable' }))
  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
  expect(screen.getByText('Image download made no progress for 15 minutes.')).toBeVisible()
  expect(posted(calls, '/api/extensions/hermes-proxy/enable?auto_enable_deps=true')).toBe(false)
})

it('downloads Open WebUI before adding it and keeps the Add button truthful', async () => {
  const webui = { id: 'open-webui', name: 'Open WebUI', source: 'core', status: 'disabled', features: [] }
  const selection = { supported: true, enabled: false }
  const calls = await show(webui, [
    { service_id: 'open-webui', status: 'pulling', phase_label: DOWNLOADING },
    { service_id: 'open-webui', status: 'prepared', phase_label: 'Images downloaded' },
  ], { webuiSelection: selection })
  fireEvent.click(screen.getByRole('button', { name: 'Available 1' }))
  fireEvent.click(screen.getByRole('button', { name: 'Add Open WebUI' }))
  fireEvent.click(screen.getByRole('button', { name: 'Add' }))
  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
  expect(screen.getByText(DOWNLOADING)).toBeVisible()
  expect(posted(calls, '/api/webui/selection')).toBe(false)
  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
  expect(posted(calls, '/api/webui/selection')).toBe(true)
  expect(screen.getByText('Open WebUI selected. Waiting for its service to become ready.')).toBeVisible()
})
