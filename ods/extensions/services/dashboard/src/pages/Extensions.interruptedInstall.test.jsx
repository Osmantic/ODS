import { act, cleanup, fireEvent, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { render } from '../test/test-utils'
import Extensions from './Extensions' // eslint-disable-line no-unused-vars

/**
 * A WSL/Docker crash during an Open WebUI add left its card showing frozen
 * install progress for about an hour (RTX Spark laptop, 2026-10-25). The API
 * now reports a record no running operation can finish as interrupted, and
 * the card offers one-click Retry and, where it applies, Cancel.
 */

const response = (data, status = 200) => ({ ok: status < 400, status, json: async () => data })
const CAUSE = 'ODS stopped before this finished, for example after a restart or a crash. Retry to continue; anything already downloaded is reused.'
const interruptedRecord = (id, extra = {}) => ({
  service_id: id, status: 'error', error: CAUSE, interrupted: true,
  interrupted_phase: 'pulling', phase_label: 'Downloading images...', ...extra,
})
const libraryApp = (overrides = {}) => ({
  id: 'gotify', name: 'Gotify', source: 'user', installable: true, status: 'error',
  interrupted: true, interrupted_operation: 'install', error_message: CAUSE,
  update_status: 'current', has_data: false, features: [], ...overrides,
})
const tick = () => act(async () => { await vi.advanceTimersByTimeAsync(3000) })
const calls = (fetchMock, path, method = 'GET') => fetchMock.mock.calls
  .filter(([url, options = {}]) => String(url) === path && (options.method || 'GET') === method).length
const card = name => within(screen.getByRole('heading', { name }).closest('article'))

async function show({ extensions, progress = {}, routes = {}, webui = { supported: false, enabled: true }, agent = true }) {
  vi.useFakeTimers()
  const state = { extensions, progress, webui }
  const fetchMock = vi.fn(async (url, options = {}) => {
    const target = String(url)
    const method = options.method || 'GET'
    const route = routes[`${method} ${target}`]
    if (route) return route(state, options)
    if (target === '/api/extensions/catalog') {
      return response({ agent_available: agent, extensions: state.extensions, summary: { total: state.extensions.length } })
    }
    if (target === '/api/templates') return response({ templates: [] })
    if (target === '/api/webui/selection' && method === 'GET') return response(state.webui)
    const progressMatch = target.match(/^\/api\/extensions\/([a-z0-9-]+)\/progress$/)
    if (progressMatch && method === 'GET') {
      return response(state.progress[progressMatch[1]] || { service_id: progressMatch[1], status: 'idle' })
    }
    throw new Error(`Unexpected request: ${method} ${target}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  await act(async () => { render(<Extensions compact />) })
  return { state, fetchMock }
}

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

it('shows an interrupted Library install with its cause, help, Retry and Cancel only', async () => {
  await show({ extensions: [libraryApp()], progress: { gotify: interruptedRecord('gotify', { operation_id: 'a'.repeat(32) }) } })
  const gotify = card('Gotify')
  expect(gotify.getByText('The install was interrupted')).toBeVisible()
  expect(gotify.getByText(new RegExp(CAUSE.slice(0, 40)))).toBeVisible()
  expect(gotify.getByRole('link', { name: 'Get help on Discord' })).toBeVisible()
  expect(gotify.getByText('interrupted')).toBeVisible()
  expect(gotify.getAllByRole('button', { name: /Retry/ })).toHaveLength(1)
  expect(gotify.getByRole('button', { name: 'Cancel' })).toBeEnabled()
  // No frozen progress line, and no second recovery path beside the block.
  expect(gotify.queryByText('Downloading images...')).toBeNull()
  expect(gotify.queryByRole('button', { name: /Remove/ })).toBeNull()
})

it('retries an interrupted Library install with one click', async () => {
  const { state, fetchMock } = await show({
    extensions: [libraryApp()],
    routes: {
      'GET /api/extensions/gotify/install-plan': () => response({ detail: 'Not Found' }, 404),
      'POST /api/extensions/gotify/install': current => {
        current.extensions = [libraryApp({ status: 'installing', interrupted: undefined, interrupted_operation: undefined })]
        current.progress.gotify = { service_id: 'gotify', status: 'pulling', phase_label: 'Downloading images... 0:15 elapsed' }
        return response({ id: 'gotify', action: 'installed' })
      },
    },
  })
  await act(async () => { fireEvent.click(card('Gotify').getByRole('button', { name: 'Retry' })) })
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(calls(fetchMock, '/api/extensions/gotify/install', 'POST')).toBe(1)

  await tick()
  expect(card('Gotify').getByText('Downloading images... 0:15 elapsed')).toBeVisible()
  expect(card('Gotify').queryByText('The install was interrupted')).toBeNull()

  state.progress.gotify = { service_id: 'gotify', status: 'started', phase_label: 'Service started' }
  state.extensions = [libraryApp({ status: 'enabled', interrupted: undefined, interrupted_operation: undefined })]
  await tick()
  expect(screen.getByText('Extension installed and started.')).toBeVisible()
})

it('cancels an interrupted Library install by removing it after its dialog', async () => {
  const { fetchMock } = await show({
    extensions: [libraryApp()],
    routes: {
      'DELETE /api/extensions/gotify': current => {
        current.extensions = [libraryApp({ source: 'library', status: 'not_installed', interrupted: undefined, interrupted_operation: undefined })]
        return response({ id: 'gotify', action: 'uninstalled', stopped_before_removal: true,
          message: 'Gotify was stopped and removed: its container and installed definition were deleted.' })
      },
    },
  })
  fireEvent.click(card('Gotify').getByRole('button', { name: 'Cancel' }))
  const dialog = within(screen.getByRole('dialog', { name: 'Confirm action' }))
  expect(dialog.getByText('Cancel Install')).toBeVisible()
  expect(dialog.getByText(/Cancel the Gotify install\? ODS will stop anything the interrupted install started/)).toBeVisible()
  expect(dialog.getByText(/Service data is kept/)).toBeVisible()
  await act(async () => { fireEvent.click(dialog.getByRole('button', { name: 'Cancel install' })) })

  expect(screen.getByText(/Gotify was stopped and removed/)).toBeVisible()
  expect(calls(fetchMock, '/api/extensions/gotify', 'DELETE')).toBe(1)
  expect(calls(fetchMock, '/api/extensions/gotify/progress', 'DELETE')).toBe(0)
})

const comfy = (overrides = {}) => ({
  id: 'comfyui', name: 'ComfyUI', source: 'core', library_manageable: true, library_selected: false,
  status: 'error', interrupted: true, interrupted_operation: null, error_message: CAUSE, features: [],
  ...overrides,
})

it('cancels an interrupted download of a bundled service without removing anything', async () => {
  const { fetchMock } = await show({
    extensions: [comfy()],
    progress: { comfyui: interruptedRecord('comfyui') },
    routes: {
      'DELETE /api/extensions/comfyui/progress': current => {
        current.extensions = [comfy({ status: 'disabled', interrupted: undefined })]
        delete current.progress.comfyui
        return response({ id: 'comfyui', action: 'dismissed', message: 'Cancelled. Nothing was removed.' })
      },
    },
  })
  expect(card('ComfyUI').queryByRole('button', { name: 'Retry ComfyUI' })).toBeNull()
  await act(async () => { fireEvent.click(card('ComfyUI').getByRole('button', { name: 'Cancel' })) })

  expect(screen.queryByRole('dialog')).toBeNull()
  expect(screen.getByText('Cancelled. Nothing was removed.')).toBeVisible()
  expect(calls(fetchMock, '/api/extensions/comfyui/progress', 'DELETE')).toBe(1)
  expect(card('ComfyUI').queryByText('The install was interrupted')).toBeNull()
  expect(card('ComfyUI').getByRole('button', { name: 'Add ComfyUI' })).toBeVisible()
})

it('retries an interrupted bundled service through its image download and enable', async () => {
  const { fetchMock } = await show({
    extensions: [comfy()],
    routes: {
      'POST /api/extensions/comfyui/prepare': () => response({ status: 'ready', service_ids: ['comfyui'] }),
      'POST /api/extensions/comfyui/enable': current => {
        current.extensions = [comfy({ status: 'enabled', library_selected: true, interrupted: undefined })]
        return response({ enabled_services: ['comfyui'], failed_services: [] })
      },
    },
  })
  await act(async () => { fireEvent.click(card('ComfyUI').getByRole('button', { name: 'Retry' })) })
  expect(calls(fetchMock, '/api/extensions/comfyui/prepare', 'POST')).toBe(1)
  expect(calls(fetchMock, '/api/extensions/comfyui/enable', 'POST')).toBe(1)
})

const webui = (overrides = {}) => ({
  id: 'open-webui', name: 'Open WebUI', source: 'core', status: 'error', interrupted: true,
  interrupted_operation: null, error_message: CAUSE, port: 3000, features: [], ...overrides,
})

it('retries an interrupted Open WebUI add as an add, not an enable', async () => {
  const { fetchMock } = await show({
    extensions: [webui()],
    webui: { supported: true, enabled: false },
    routes: {
      'POST /api/extensions/open-webui/prepare': () => response({ status: 'ready', service_ids: ['open-webui'] }),
      'POST /api/webui/selection': current => {
        current.webui = { supported: true, enabled: true }
        current.extensions = [webui({ status: 'enabled', interrupted: undefined })]
        return response({ enabled: true, action: 'enabled' })
      },
    },
  })
  const openWebui = card('Open WebUI')
  expect(openWebui.getByText('The install was interrupted')).toBeVisible()
  expect(openWebui.queryByRole('button', { name: 'Add Open WebUI' })).toBeNull()
  expect(openWebui.getAllByRole('button', { name: /Retry/ })).toHaveLength(1)

  await act(async () => { fireEvent.click(openWebui.getByRole('button', { name: 'Retry' })) })
  expect(calls(fetchMock, '/api/webui/selection', 'POST')).toBe(1)
  expect(calls(fetchMock, '/api/extensions/open-webui/enable', 'POST')).toBe(0)
})

it('ends a download that ODS interrupted with Retry and Cancel on the card', async () => {
  const { state, fetchMock } = await show({
    extensions: [webui({ status: 'disabled', interrupted: undefined, error_message: undefined })],
    webui: { supported: true, enabled: false },
    progress: { 'open-webui': { service_id: 'open-webui', status: 'pulling', phase_label: 'Downloading images... 3:15 elapsed, 12 layers done' } },
    routes: {
      'POST /api/extensions/open-webui/prepare': () => response({ status: 'accepted', service_ids: ['open-webui'] }, 202),
    },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Add Open WebUI' }))
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Add' })) })
  await tick()
  expect(screen.getByText('Downloading images... 3:15 elapsed, 12 layers done')).toBeVisible()

  // WSL and Docker Desktop restart: the dashboard does not answer for a while.
  const answer = fetchMock.getMockImplementation()
  fetchMock.mockImplementation(async (url, options) => (
    String(url) === '/api/extensions/open-webui/progress' ? response({}, 502) : answer(url, options)))
  await tick()
  await tick()
  await tick()
  expect(screen.getByText(/Connection to dashboard lost/)).toBeVisible()

  // ODS is back; the host agent marked the download it lost as interrupted.
  fetchMock.mockImplementation(answer)
  state.progress['open-webui'] = interruptedRecord('open-webui')
  state.extensions = [webui()]
  await tick()
  expect(screen.queryByText(/Connection to dashboard lost/)).toBeNull()
  expect(screen.getAllByText(new RegExp(CAUSE.slice(0, 40))).length).toBeGreaterThan(0)
  const openWebui = card('Open WebUI')
  expect(openWebui.getByText('The install was interrupted')).toBeVisible()
  expect(openWebui.getByRole('button', { name: 'Retry' })).toBeEnabled()
  expect(openWebui.queryByText('Downloading images... 3:15 elapsed, 12 layers done')).toBeNull()
  expect(calls(fetchMock, '/api/webui/selection', 'POST')).toBe(0)
})

it('stops following an install whose record became interrupted', async () => {
  const { state, fetchMock } = await show({
    extensions: [libraryApp({ status: 'installing', interrupted: undefined, interrupted_operation: undefined })],
    progress: { gotify: { service_id: 'gotify', status: 'starting', phase_label: 'Starting container...' } },
  })
  await tick()
  expect(screen.getByText('Starting container...')).toBeVisible()

  state.progress.gotify = interruptedRecord('gotify', { interrupted_phase: 'starting' })
  state.extensions = [libraryApp()]
  await tick()
  expect(screen.getByText('The Gotify install was interrupted. Retry or cancel it on its card.')).toBeVisible()
  expect(card('Gotify').getByText('The install was interrupted')).toBeVisible()

  const polls = calls(fetchMock, '/api/extensions/gotify/progress')
  await tick()
  expect(calls(fetchMock, '/api/extensions/gotify/progress')).toBe(polls)
})

it('reports success when the interrupted install had already started the service', async () => {
  const { state } = await show({
    extensions: [libraryApp({ status: 'installing', interrupted: undefined, interrupted_operation: undefined })],
    progress: { gotify: { service_id: 'gotify', status: 'starting', phase_label: 'Starting container...' } },
  })
  await tick()
  state.progress.gotify = interruptedRecord('gotify', { interrupted_phase: 'starting' })
  state.extensions = [libraryApp({ status: 'enabled', interrupted: undefined, interrupted_operation: undefined })]
  await tick()
  expect(screen.getByText('Extension installed and started.')).toBeVisible()
  expect(screen.queryByText(/install was interrupted/)).toBeNull()
})

it('says the install waits for the host service while the agent is offline', async () => {
  await show({
    agent: false,
    extensions: [libraryApp({ status: 'installing', interrupted: undefined, interrupted_operation: undefined })],
    progress: { gotify: { service_id: 'gotify', status: 'pulling', phase_label: 'Downloading images... 1:00 elapsed' } },
  })
  await tick()
  expect(card('Gotify').getByText('Waiting for the ODS host service to respond…')).toBeVisible()
  expect(card('Gotify').queryByText('Downloading images... 1:00 elapsed')).toBeNull()
  expect(screen.getByText(/Host agent is offline/).closest('div')).toHaveTextContent("ods agent restart")
  expect(within(screen.getByText(/Host agent is offline/).closest('div')).getByRole('link', { name: 'Get help on Discord' })).toBeVisible()
})

it('says on the Add dialog and the card that adding Open WebUI is permanent', async () => {
  const { state } = await show({
    extensions: [webui({ status: 'disabled', interrupted: undefined, error_message: undefined })],
    webui: { supported: true, enabled: false },
    routes: {
      'POST /api/extensions/open-webui/prepare': () => response({ status: 'ready', service_ids: ['open-webui'] }),
      'POST /api/webui/selection': current => {
        current.webui = { supported: true, enabled: true }
        current.extensions = [webui({ status: 'enabled', interrupted: undefined, error_message: undefined })]
        return response({ enabled: true, action: 'enabled' })
      },
    },
  })
  expect(card('Open WebUI').queryByText('Stays on once added')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Add Open WebUI' }))
  expect(screen.getByRole('dialog', { name: 'Confirm action' })).toHaveTextContent(
    'Adding is permanent: once added, Open WebUI stays on, and the dashboard cannot remove it.')
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Add' })) })
  expect(state.webui.enabled).toBe(true)
  expect(card('Open WebUI').getByText('Stays on once added')).toBeVisible()
  expect(card('Open WebUI').queryByRole('button', { name: /Remove|Disable/ })).toBeNull()
})
