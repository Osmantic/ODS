import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Models from './Models'

const useModelsMock = vi.fn()
vi.mock('../hooks/useModels', () => ({ useModels: () => useModelsMock() }))

const model = 'hf-bartowski-Llama-3.2-3B-Instruct-GGUF-Llama-3.2-3B-Instruct-Q4_K_M-33b9fba8.gguf'
const fallback = { status: 'downloading', model, bytesDownloaded: 0, bytesTotal: 0,
  updatedAt: '2026-10-17T17:59:54.093246+00:00',
  error: 'Retry 1/3: curl exited with code 35: curl: (35) Recv failure: Connection was reset; Hugging Face Hub fallback active' }
let status
let refresh

beforeEach(() => {
  status = { ...fallback }
  refresh = vi.fn()
  useModelsMock.mockReturnValue({
    models: [], gpu: { vramUsed: 2, vramTotal: 8, vramFree: 6 },
    currentModel: null, configuredModel: null, odsMode: 'local', configuredMode: 'local',
    canActivateModels: true, recommendationAlternatives: [], hermesMinimumContext: 65536,
    pixelMinimumContext: 16384, loading: false, error: null, actionLoading: null,
    activationLoading: null, downloadModel: vi.fn(), loadModel: vi.fn(),
    benchmarkModel: vi.fn(), deleteModel: vi.fn(), refresh,
  })
  Object.defineProperty(document, 'hidden', { configurable: true, value: false })
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    if (url === '/api/models/download/cancel' && options?.method === 'POST') {
      status = { status: 'idle', lastTerminalStatus: { status: 'cancelled', model,
        updatedAt: '2026-10-17T18:00:00.000Z', error: 'Download cancelled by user' } }
      return { ok: true }
    }
    if (url === '/api/models/download-status') return { ok: true, json: async () => status }
    throw new Error('Unexpected test request')
  }))
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  delete document.hidden
})

function showModels() {
  return render(<MemoryRouter><Models /></MemoryRouter>)
}

async function updateStatus(next) {
  status = next
  await act(async () => { document.dispatchEvent(new Event('visibilitychange')) })
}

test.each(['downloading', 'verifying'])('unknown %s progress is indeterminate through the real API hook', async (phase) => {
  status.status = phase
  showModels()
  expect(await screen.findByText('Progress unavailable')).toBeVisible()
  expect(screen.getByRole('progressbar', { name: 'Download progress' })).not.toHaveAttribute('aria-valuenow')
  expect(screen.queryByText('0 B / 0 B')).not.toBeInTheDocument()
  expect(screen.queryByText('0%')).not.toBeInTheDocument()
  expect(screen.queryByText('Download Failed')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Cancel', exact: true })).toBeEnabled()
})

test('an observed byte count without a total stays visible without an invented percentage', async () => {
  status = { ...fallback, bytesDownloaded: 512, bytesTotal: undefined }
  showModels()
  expect(await screen.findByText('512 B downloaded · Total size unavailable')).toBeVisible()
  expect(screen.getByRole('progressbar', { name: 'Download progress' })).not.toHaveAttribute('aria-valuenow')
  expect(screen.queryByText('0%')).not.toBeInTheDocument()
})

test.each([[0, '0%', '0 B'], [512, '50%', '512 B']])('known totals retain measured progress at %i bytes', async (bytesDownloaded, percent, bytes) => {
  status = { ...fallback, bytesDownloaded, bytesTotal: 1024 }
  showModels()
  expect(await screen.findByText(percent)).toBeVisible()
  expect(screen.getByText(`${bytes} / 1 KB`)).toBeVisible()
  expect(screen.queryByText('Progress unavailable')).not.toBeInTheDocument()
})

test('unknown progress can become measured, verify, and complete without changing terminal handling', async () => {
  showModels()
  await screen.findByRole('button', { name: 'Cancel', exact: true })
  await updateStatus({ ...fallback, bytesDownloaded: 512, bytesTotal: 1024 })
  expect(await screen.findByText('50%')).toBeVisible()
  await updateStatus({ ...fallback, status: 'verifying', bytesDownloaded: 1024, bytesTotal: 1024 })
  expect(await screen.findByText(`Verifying ${model}`)).toBeVisible()
  expect(screen.getByText('100%')).toBeVisible()
  await updateStatus({ status: 'complete', model, bytesDownloaded: 0, bytesTotal: 0,
    updatedAt: '2026-10-17T18:00:06.996677+00:00' })
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Cancel', exact: true })).not.toBeInTheDocument())
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
  expect(refresh).toHaveBeenCalled()
})

test('an indeterminate download still uses the real cancellation endpoint and terminal receipt', async () => {
  showModels()
  fireEvent.click(await screen.findByRole('button', { name: 'Cancel', exact: true }))
  expect(await screen.findByText('Download Cancelled')).toBeVisible()
  expect(screen.getByText('Download cancelled by user')).toBeVisible()
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
  expect(fetch).toHaveBeenCalledWith('/api/models/download/cancel', expect.objectContaining({ method: 'POST' }))
})

test('a real terminal failure replaces indeterminate progress', async () => {
  showModels()
  await screen.findByRole('button', { name: 'Cancel', exact: true })
  await updateStatus({ status: 'failed', model, error: 'Downloaded file checksum did not match' })
  expect(await screen.findByText('Download Failed')).toBeVisible()
  expect(screen.getByText('Downloaded file checksum did not match')).toBeVisible()
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
})
