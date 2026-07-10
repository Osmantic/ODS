import { createElement } from 'react'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Models from './Models'

const useModelsMock = vi.fn()
const useDownloadProgressMock = vi.fn()

vi.mock('../hooks/useModels', () => ({
  useModels: () => useModelsMock(),
}))

vi.mock('../hooks/useDownloadProgress', () => ({
  useDownloadProgress: () => useDownloadProgressMock(),
}))

function baseDownloadState(overrides = {}) {
  return {
    isDownloading: false,
    progress: null,
    completedDownload: null,
    refresh: vi.fn(),
    cancelDownload: vi.fn(),
    clearTerminal: vi.fn(),
    formatBytes: (value) => `${value} B`,
    formatEta: (value) => `${value}s`,
    ...overrides,
  }
}

beforeEach(() => {
  useDownloadProgressMock.mockReturnValue(baseDownloadState())
})

function baseState(overrides = {}) {
  return {
    models: [],
    gpu: { vramUsed: 2, vramTotal: 8, vramFree: 6 },
    currentModel: null,
    configuredModel: null,
    odsMode: 'local',
    recommendationAlternatives: [],
    loading: false,
    error: null,
    actionLoading: null,
    downloadModel: vi.fn(),
    loadModel: vi.fn(),
    benchmarkModel: vi.fn(),
    deleteModel: vi.fn(),
    refresh: vi.fn(),
    ...overrides,
  }
}

function model(overrides = {}) {
  return {
    id: 'qwen3.5-9b-q4',
    name: 'Qwen 3.5 9B',
    size: '5.6 GB',
    sizeGb: 5.6,
    vramRequired: 7,
    contextLength: 32768,
    specialty: 'General',
    description: 'Balanced local model.',
    quantization: 'Q4_K_M',
    status: 'available',
    fitsVram: true,
    tokensPerSec: 51.7,
    ...overrides,
  }
}

function renderModels() {
  return render(createElement(MemoryRouter, null, createElement(Models)))
}

test('renders the model library layout from catalog fields only', () => {
  useModelsMock.mockReturnValue(baseState({
    currentModel: 'qwen3.5-9b-q4',
    models: [
      model({ status: 'loaded', recommended: true }),
      model({
        id: 'phi4-mini-q4',
        name: 'Phi-4 Mini',
        size: '2.4 GB',
        sizeGb: 2.4,
        vramRequired: 3,
        estimatedRequired: 3.2,
        contextLength: 128000,
        specialty: 'Reasoning',
        description: 'Compact reasoning model.',
        tokensPerSec: 69.8,
      }),
    ],
  }))

  renderModels()

  expect(screen.getByRole('button', { name: /model library/i })).toBeInTheDocument()
  expect(screen.getByText('VRAM')).toBeInTheDocument()
  expect(screen.getByText('Speed')).toBeInTheDocument()
  expect(screen.getByText('Currently running: qwen3.5-9b-q4')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /dashboard/i })).toHaveAttribute('href', '/')
  expect(screen.getByText('51.7 tok/s')).toBeInTheDocument()
  expect(screen.getByText('69.8 tok/s')).toBeInTheDocument()
  expect(screen.getByText('~3.2 GB incl. KV')).toBeInTheDocument()
})

test('loaded models show active state and benchmark action', () => {
  const benchmarkModel = vi.fn()
  useModelsMock.mockReturnValue(baseState({
    currentModel: 'qwen3.5-9b-q4',
    benchmarkModel,
    models: [model({ status: 'loaded' })],
  }))

  renderModels()

  expect(screen.getByText('Active')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /benchmark/i }))
  expect(benchmarkModel).toHaveBeenCalledWith('qwen3.5-9b-q4')

  fireEvent.click(screen.getByRole('button', { name: /model actions/i }))
  expect(screen.queryByRole('button', { name: /delete file/i })).not.toBeInTheDocument()
})

test('renders oracle source labels and install recommendation context', () => {
  useModelsMock.mockReturnValue(baseState({
    configuredModel: 'qwen3.5-9b-q4',
    recommendationAlternatives: [
      { id: 'qwen3.5-9b-q4', name: 'Qwen 3.5 9B' },
      { id: 'deepseek-r1-7b-q4', name: 'DeepSeek R1 7B' },
    ],
    models: [
      model({
        recommended: true,
        performanceLabel: 'Benchmark after first launch',
        performance: { source: 'benchmark_required' },
      }),
      model({
        id: 'phi4-mini-q4',
        name: 'Phi-4 Mini',
        size: '2.4 GB',
        sizeGb: 2.4,
        vramRequired: 4,
        estimatedRequired: 4.4,
        contextLength: 128000,
        specialty: 'Balanced',
        description: 'Compact model.',
        performanceLabel: '32.1 tok/s measured locally',
        performance: { source: 'measured_local' },
      }),
    ],
  }))

  renderModels()

  expect(screen.getByText('Benchmark after first launch')).toBeInTheDocument()
  expect(screen.getByText('Benchmark required')).toBeInTheDocument()
  expect(screen.getByText(/Top catalog fit: Qwen 3.5 9B/)).toBeInTheDocument()
  expect(screen.getByText('Selected install')).toBeInTheDocument()
  expect(screen.getByText('Measured locally')).toBeInTheDocument()
  expect(screen.getByText('~4.4 GB incl. KV')).toBeInTheDocument()
})

test('runs downloaded models through the existing load action', () => {
  const loadModel = vi.fn()
  useModelsMock.mockReturnValue(baseState({
    loadModel,
    models: [model({ status: 'downloaded' })],
  }))

  renderModels()
  fireEvent.click(screen.getByRole('button', { name: /^run$/i }))

  expect(loadModel).toHaveBeenCalledWith('qwen3.5-9b-q4')

  fireEvent.click(screen.getByRole('button', { name: /model actions/i }))
  expect(screen.getByRole('button', { name: /delete file/i })).toBeInTheDocument()
})

test('keeps Download available in cloud mode', () => {
  const downloadModel = vi.fn()
  useModelsMock.mockReturnValue(baseState({
    odsMode: 'cloud',
    downloadModel,
    models: [model()],
  }))

  renderModels()
  const downloadButton = screen.getByRole('button', { name: /^download$/i })
  expect(downloadButton).toBeEnabled()
  fireEvent.click(downloadButton)

  expect(downloadModel).toHaveBeenCalledWith('qwen3.5-9b-q4')
})

test('shows terminal download failures with a retry action', async () => {
  const downloadModel = vi.fn()
  const clearTerminal = vi.fn()
  useModelsMock.mockReturnValue(baseState({
    downloadModel,
    models: [model()],
  }))
  useDownloadProgressMock.mockReturnValue(baseDownloadState({
    progress: {
      status: 'failed',
      model: 'qwen3.5-9b-q4',
      error: 'The download checksum did not match.',
    },
    clearTerminal,
  }))

  renderModels()

  expect(screen.getByText('Download Failed')).toBeInTheDocument()
  expect(screen.getByText('The download checksum did not match.')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /retry/i }))

  expect(clearTerminal).toHaveBeenCalled()
  expect(downloadModel).toHaveBeenCalledWith('qwen3.5-9b-q4')
  await act(async () => {})
})

test('shows a cancel control while downloading', () => {
  const cancelDownload = vi.fn()
  useModelsMock.mockReturnValue(baseState({ models: [model()] }))
  useDownloadProgressMock.mockReturnValue(baseDownloadState({
    isDownloading: true,
    progress: {
      status: 'downloading',
      model: 'qwen3.5-9b-q4',
      bytesDownloaded: 5,
      bytesTotal: 10,
      percent: 50,
      speedMbps: 1,
      eta: 5,
    },
    cancelDownload,
  }))

  renderModels()
  fireEvent.click(screen.getByRole('button', { name: /cancel/i }))

  expect(cancelDownload).toHaveBeenCalledTimes(1)
})

test('recovers from Download Starting when status remains idle', async () => {
  vi.useFakeTimers()
  const downloadModel = vi.fn().mockResolvedValue(undefined)
  const refresh = vi.fn().mockResolvedValue({ status: 'idle' })
  useModelsMock.mockReturnValue(baseState({
    downloadModel,
    models: [model()],
  }))
  useDownloadProgressMock.mockReturnValue(baseDownloadState({ refresh }))

  try {
    renderModels()
    fireEvent.click(screen.getByRole('button', { name: /^download$/i }))
    await act(async () => {})
    expect(screen.getByRole('button', { name: /starting/i })).toBeDisabled()

    await act(async () => { await vi.advanceTimersByTimeAsync(15000) })

    expect(screen.queryByRole('button', { name: /starting/i })).not.toBeInTheDocument()
    expect(screen.getByText(/did not start within 15 seconds/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /retry/i })).toBeEnabled()
    expect(refresh).toHaveBeenCalledTimes(2)
  } finally {
    vi.useRealTimers()
  }
})

test('disables model actions and hides Delete while that model is working', () => {
  useModelsMock.mockReturnValue(baseState({
    actionLoading: 'qwen3.5-9b-q4',
    actionLoadingModels: ['qwen3.5-9b-q4'],
    models: [model({ status: 'downloaded' })],
  }))

  renderModels()

  expect(screen.getByRole('button', { name: /working/i })).toBeDisabled()
  expect(screen.getByRole('button', { name: /model actions/i })).toBeDisabled()
  expect(screen.queryByRole('button', { name: /delete file/i })).not.toBeInTheDocument()
})

test('disables primary and menu Run actions in cloud mode', () => {
  const loadModel = vi.fn()
  useModelsMock.mockReturnValue(baseState({
    odsMode: 'cloud',
    loadModel,
    models: [model({ status: 'downloaded' })],
  }))

  renderModels()

  const primaryRun = screen.getByRole('button', { name: /^run$/i })
  expect(primaryRun).toBeDisabled()
  expect(primaryRun).toHaveAttribute('title', 'Switch ODS to local mode to run this model.')
  fireEvent.click(primaryRun)

  fireEvent.click(screen.getByRole('button', { name: /model actions/i }))
  const menuRun = screen.getByRole('button', { name: /run model/i })
  expect(menuRun).toBeDisabled()
  expect(menuRun).toHaveAttribute('title', 'Switch ODS to local mode to run this model.')
  fireEvent.click(menuRun)

  expect(loadModel).not.toHaveBeenCalled()
})

test('treats currentModel as active even if a stale row still says downloaded', () => {
  useModelsMock.mockReturnValue(baseState({
    currentModel: 'qwen3.5-9b-q4',
    models: [model({ status: 'downloaded' })],
  }))

  renderModels()

  expect(screen.getByText('Active')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /benchmark/i })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /model actions/i }))
  expect(screen.queryByRole('button', { name: /delete file/i })).not.toBeInTheDocument()
})

test('filters models by search and category without changing catalog data', () => {
  useModelsMock.mockReturnValue(baseState({
    models: [
      model(),
      model({
        id: 'qwen3-coder-next-q4',
        name: 'Qwen 3 Coder Next',
        size: '47.4 GB',
        sizeGb: 47.4,
        vramRequired: 54,
        contextLength: 131072,
        specialty: 'Code',
        description: 'Large coding model for repositories.',
        fitsVram: false,
        tokensPerSec: 12.4,
      }),
    ],
  }))

  renderModels()

  fireEvent.click(screen.getByTestId('model-category-code'))

  expect(screen.getByText('Qwen 3 Coder Next')).toBeInTheDocument()
  expect(screen.queryByText('Qwen 3.5 9B')).not.toBeInTheDocument()

  fireEvent.change(screen.getByPlaceholderText('Search models...'), { target: { value: '9B' } })

  expect(screen.getByText('No models match the current filters.')).toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: /reset/i }))
  expect(screen.getByText('Qwen 3.5 9B')).toBeInTheDocument()
})
