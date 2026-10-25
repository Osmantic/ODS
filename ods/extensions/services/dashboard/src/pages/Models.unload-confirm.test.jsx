import { StrictMode, createElement } from 'react'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Models from './Models'

// The production model hook drives the page, so these tests see the requests
// the runtime controls actually send. Downloads are a separate transport.
vi.mock('../hooks/useDownloadProgress', () => ({
  useDownloadProgress: () => ({ isDownloading: false, refresh: vi.fn() }),
}))

const modelId = 'qwen3.5-9b-q4'
const json = (body, status = 200) => ({ ok: status < 400, status, json: async () => body })
const posts = () => fetch.mock.calls.filter(([, options]) => options?.method === 'POST')
let running

// A model on the Windows host that this ODS installation manages.
const catalog = () => ({
  models: [{
    id: modelId, name: 'Qwen 3.5 9B', status: running ? 'loaded' : 'downloaded',
    size: '5.6 GB', sizeGb: 5.6, vramRequired: 7, fitsVram: true,
    contextLength: 65536, maxContextLength: 65536, specialty: 'General',
    quantization: 'Q4_K_M', publisher: { name: 'Qwen' },
  }],
  currentModel: running ? modelId : null,
  gpu: { vramTotal: 16, vramFree: 8, vramUsed: 8 },
  odsMode: 'local', configuredMode: 'local', llmBackend: 'llama-server', hostRuntime: true,
  modelManagement: { managed: true, canActivate: running, canUnload: true, running },
})

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    if (url === '/api/models') return json(catalog())
    if (options?.method === 'POST' && url === '/api/models/runtime/stop') {
      running = false
      return json({ status: 'stopped' })
    }
    if (options?.method === 'POST' && url === '/api/models/runtime/start') {
      running = true
      return json({ status: 'started' })
    }
    return json({}, 404)
  }))
})

afterEach(() => {
  vi.unstubAllGlobals()
})

async function openRuntimeControls() {
  render(createElement(StrictMode, null, createElement(MemoryRouter, null, createElement(Models))))
  return within(await screen.findByRole('region', { name: 'Model runtime controls' }))
}

test('Unload model asks first, Cancel sends nothing, and confirming stops the runtime once', async () => {
  running = true
  const controls = await openRuntimeControls()

  fireEvent.click(controls.getByRole('button', { name: 'Unload model' }))
  const dialog = screen.getByRole('dialog', { name: 'Unload the model?' })
  expect(dialog).toHaveAccessibleDescription('Portal and Hermes stop answering until you choose Resume model. Loading it again can take a few minutes.')
  expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus()
  expect(posts()).toEqual([])

  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(controls.getByRole('button', { name: 'Unload model' })).toBeEnabled()
  expect(posts()).toEqual([])

  fireEvent.click(controls.getByRole('button', { name: 'Unload model' }))
  fireEvent.click(within(screen.getByRole('dialog', { name: 'Unload the model?' })).getByRole('button', { name: 'Unload model' }))
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(await controls.findByRole('button', { name: 'Resume model' })).toBeEnabled()
  expect(posts().map(([url]) => url)).toEqual(['/api/models/runtime/stop'])
})

test('Resume model starts a stopped runtime without asking', async () => {
  running = false
  const controls = await openRuntimeControls()

  fireEvent.click(controls.getByRole('button', { name: 'Resume model' }))
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(await controls.findByRole('button', { name: 'Unload model' })).toBeEnabled()
  expect(posts().map(([url]) => url)).toEqual(['/api/models/runtime/start'])
})
