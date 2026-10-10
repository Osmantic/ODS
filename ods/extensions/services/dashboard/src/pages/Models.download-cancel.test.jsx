import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Models from './Models'

const useModelsMock = vi.fn()
vi.mock('../hooks/useModels', () => ({ useModels: () => useModelsMock() }))

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  delete document.hidden
})

test('real progress hook exposes Download Cancelled and Retry for the API inactive cancellation envelope', async () => {
  const modelId = 'qwen3.5-9b-q4'
  const file = 'Qwen3.5-9B-Q4_K_M.gguf'
  let status = { status: 'verifying', model: file, bytesTotal: 5680522464,
    updatedAt: '2026-10-08T03:47:47.000Z' }
  const downloadModel = vi.fn(async () => {
    status = { status: 'verifying', model: file, updatedAt: '2026-10-08T03:48:00.000Z' }
  })
  useModelsMock.mockReturnValue({
    models: [{ id: modelId, name: 'Qwen 3.5 9B', gguf: file, status: 'downloaded', fitsVram: true,
      size: '5.6 GB', sizeGb: 5.6, vramRequired: 7, contextLength: 65536, specialty: 'General',
      description: 'Local model.', quantization: 'Q4_K_M', publisher: { name: 'Qwen' } }],
    gpu: { vramUsed: 2, vramTotal: 8, vramFree: 6 }, currentModel: null, configuredModel: null,
    odsMode: 'local', configuredMode: 'local', canActivateModels: true,
    recommendationAlternatives: [], hermesMinimumContext: 65536, pixelMinimumContext: 16384,
    loading: false, error: null, actionLoading: null, activationLoading: null,
    downloadModel, loadModel: vi.fn(), benchmarkModel: vi.fn(), deleteModel: vi.fn(), refresh: vi.fn(),
  })
  Object.defineProperty(document, 'hidden', { configurable: true, value: false })
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    if (url === '/api/models/download/cancel' && options?.method === 'POST') {
      status = { status: 'idle', active: false, isDownloading: false, lastTerminalStatus: {
        status: 'cancelled', model: file, updatedAt: '2026-10-08T03:47:48.365Z',
        error: 'Download cancelled by user',
      } }
      return { ok: true }
    }
    if (url === '/api/models/download-status') return { ok: true, json: async () => status }
    throw new Error('Unexpected test request')
  }))
  render(<MemoryRouter><Models /></MemoryRouter>)
  fireEvent.click(await screen.findByRole('button', { name: 'Cancel', exact: true }))
  expect(await screen.findByText('Download Cancelled')).toBeVisible()
  expect(screen.getByText('Download cancelled by user')).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Retry', exact: true }))
  await waitFor(() => expect(downloadModel).toHaveBeenCalledWith(modelId))
  await waitFor(() => expect(screen.queryByText('Download Cancelled')).not.toBeInTheDocument())
  expect(await screen.findByRole('button', { name: 'Cancel', exact: true })).toBeVisible()
  status = { status: 'complete', model: file, updatedAt: '2026-10-08T03:48:03.000Z' }
  await act(async () => { document.dispatchEvent(new Event('visibilitychange')) })
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Cancel', exact: true })).not.toBeInTheDocument())
})
