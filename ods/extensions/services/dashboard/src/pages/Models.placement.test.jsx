import { createElement } from 'react'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Models from './Models'

const useModelsMock = vi.fn()

vi.mock('../hooks/useModels', () => ({
  useModels: () => useModelsMock(),
}))

vi.mock('../hooks/useDownloadProgress', () => ({
  useDownloadProgress: () => ({
    isDownloading: false,
    progress: null,
    completedDownload: null,
    statusError: null,
    refresh: vi.fn(),
    formatBytes: value => `${value} B`,
    formatEta: value => `${value}s`,
  }),
}))

const LAPTOP_PARTIAL = {
  layersOnGpu: 29,
  layersTotal: 33,
  cpuWeightMiB: 1078.96,
  fullyResident: false,
  intentionalOffload: false,
  state: 'partial',
  detail: 'llama.cpp projected 6492 MiB against 6860 MiB free with a 1024 MiB margin',
}
const RESIDENT = { ...LAPTOP_PARTIAL, layersOnGpu: 33, fullyResident: true, state: 'resident', detail: null }

function loadedModel(overrides = {}) {
  return {
    id: 'qwen3.5-9b-q4',
    name: 'Qwen 3.5 9B',
    size: '5.6 GB',
    sizeGb: 5.6,
    vramRequired: 7,
    contextLength: 65536,
    specialty: 'General',
    description: 'Balanced local model.',
    quantization: 'Q4_K_M',
    publisher: { name: 'Qwen', huggingFaceAuthor: 'Qwen' },
    status: 'loaded',
    fitsVram: true,
    fitLabel: 'Fits 8 GB',
    tokensPerSec: 21.4,
    ...overrides,
  }
}

function state(runtimePlacement) {
  return {
    models: [loadedModel()],
    gpu: { vramUsed: 6.1, vramTotal: 7.96, vramFree: 1.86 },
    currentModel: 'qwen3.5-9b-q4',
    loadedModel: 'Qwen3.5-9B-Q4_K_M.gguf',
    configuredModel: 'qwen3.5-9b-q4',
    runtimePlacement,
    odsMode: 'local',
    configuredMode: 'local',
    canActivateModels: true,
    activationModeError: null,
    recommendationAlternatives: [],
    hermesMinimumContext: 65536,
    pixelMinimumContext: 16384,
    loading: false,
    error: null,
    actionLoading: null,
    activationLoading: null,
    downloadModel: vi.fn(),
    loadModel: vi.fn(),
    benchmarkModel: vi.fn(),
    deleteModel: vi.fn(),
    refresh: vi.fn(),
  }
}

function renderModels(compact = false) {
  return render(createElement(MemoryRouter, null, createElement(Models, { compact })))
}

const WARNING = 'Model partly on CPU (29/33 layers on GPU) — expect slower responses'

test('warns when the running model is partly on the CPU and drops the fit badge', () => {
  useModelsMock.mockReturnValue(state(LAPTOP_PARTIAL))
  renderModels()

  const warning = screen.getByRole('status')
  expect(within(warning).getByText(WARNING)).toBeVisible()
  expect(within(warning).getByText(/1\.1 GB of the model is in system memory\./)).toBeVisible()
  expect(within(warning).getByText(LAPTOP_PARTIAL.detail)).toBeVisible()
  // The catalog estimate said it fits; the running server says otherwise.
  expect(screen.queryByText('Fits 8 GB')).not.toBeInTheDocument()
  expect(screen.getByText('Partly on CPU')).toBeInTheDocument()
})

test('shows full GPU residency next to the running model', () => {
  useModelsMock.mockReturnValue(state(RESIDENT))
  renderModels()

  expect(screen.getByText('GPU: 33/33 layers')).toBeInTheDocument()
  expect(screen.getByText('Fits 8 GB')).toBeInTheDocument()
  expect(screen.queryByText(WARNING)).not.toBeInTheDocument()
  expect(screen.queryByText('Partly on CPU')).not.toBeInTheDocument()
})

test('a configured CPU offload is labelled, not flagged', () => {
  useModelsMock.mockReturnValue(state({ ...RESIDENT, intentionalOffload: true, state: 'intentional' }))
  renderModels()

  expect(screen.getByText('GPU: 33/33 layers · CPU offload configured')).toBeInTheDocument()
  expect(screen.queryByText(WARNING)).not.toBeInTheDocument()
})

test('a CPU-only load on a GPU host gets the stronger warning', () => {
  useModelsMock.mockReturnValue(state({ ...LAPTOP_PARTIAL, layersOnGpu: 0, state: 'cpu_only' }))
  renderModels()

  expect(screen.getByRole('status')).toHaveTextContent(
    'Model running on the CPU only (0/33 layers on GPU) — expect much slower responses',
  )
  expect(screen.getByText('CPU only')).toBeInTheDocument()
  expect(screen.queryByText('Fits 8 GB')).not.toBeInTheDocument()
})

test('older hosts without placement show no placement claim either way', () => {
  useModelsMock.mockReturnValue(state(null))
  renderModels()

  expect(screen.queryByText(/layers/)).not.toBeInTheDocument()
  expect(screen.queryByText(WARNING)).not.toBeInTheDocument()
  expect(screen.getByText('Fits 8 GB')).toBeInTheDocument()
})

test('compact panel reports GPU layers and the partial-offload warning', () => {
  useModelsMock.mockReturnValue(state(LAPTOP_PARTIAL))
  renderModels(true)

  const panel = screen.getByRole('region', { name: 'Model runtime' })
  expect(within(panel).getByText('GPU layers')).toBeVisible()
  expect(within(panel).getByText('29/33')).toBeVisible()
  expect(within(panel).getByRole('status')).toHaveTextContent(WARNING)
})

test('no placement is shown when nothing is running', () => {
  useModelsMock.mockReturnValue({
    ...state(LAPTOP_PARTIAL),
    currentModel: null,
    loadedModel: null,
    models: [loadedModel({ status: 'downloaded' })],
  })
  renderModels()

  expect(screen.queryByText(WARNING)).not.toBeInTheDocument()
})
