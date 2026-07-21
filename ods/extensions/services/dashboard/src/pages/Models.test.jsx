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
    cancelError: null,
    isCancelling: false,
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
    configuredMode: 'local',
    canActivateModels: true,
    activationModeError: null,
    recommendationAlternatives: [],
    hermesMinimumContext: 65536,
    loading: false,
    error: null,
    actionLoading: null,
    activationLoading: null,
    downloadModel: vi.fn(),
    loadModel: vi.fn(),
    benchmarkModel: vi.fn(),
    deleteModel: vi.fn(),
    refresh: vi.fn(),
    ...overrides,
  }
}

function deferred() {
  let resolve
  let reject
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function model(overrides = {}) {
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
  expect(screen.getAllByText('VRAM').length).toBeGreaterThan(0)
  expect(screen.getAllByText('Speed').length).toBeGreaterThan(0)
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
  const deleteButton = screen.getByRole('button', { name: /delete qwen 3\.5 9b unavailable/i })
  expect(deleteButton).toBeDisabled()
  expect(deleteButton).toHaveAttribute('title', 'The active model cannot be deleted. Run another model first.')
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

test('keeps Run and Delete visible for downloaded models', () => {
  const loadModel = vi.fn()
  const deleteModel = vi.fn()
  useModelsMock.mockReturnValue(baseState({
    loadModel,
    deleteModel,
    models: [model({ status: 'downloaded' })],
  }))

  renderModels()
  fireEvent.click(screen.getByRole('button', { name: /^run$/i }))

  expect(loadModel).toHaveBeenCalledWith('qwen3.5-9b-q4')
  const deleteButton = screen.getByRole('button', { name: /delete qwen 3\.5 9b$/i })
  expect(deleteButton).toBeEnabled()
  fireEvent.click(deleteButton)
  expect(deleteModel).not.toHaveBeenCalled()

  expect(screen.getByRole('dialog', { name: /delete qwen 3\.5 9b/i })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /delete model/i }))
  expect(deleteModel).toHaveBeenCalledWith('qwen3.5-9b-q4')
})

test('allows low-context downloaded models to run with an agent-readiness warning', () => {
  const loadModel = vi.fn()
  const deleteModel = vi.fn()
  useModelsMock.mockReturnValue(baseState({
    loadModel,
    deleteModel,
    models: [model({ status: 'downloaded', contextLength: 8192 })],
  }))

  renderModels()

  const runButton = screen.getByRole('button', { name: /^run$/i })
  expect(runButton).toBeEnabled()
  expect(runButton).toHaveAttribute('title', 'Run Qwen 3.5 9B')
  fireEvent.click(runButton)
  expect(loadModel).toHaveBeenCalledWith('qwen3.5-9b-q4')
  expect(screen.getByText('Direct chat only')).toBeInTheDocument()
  expect(screen.getByText('Needs 64K')).toBeInTheDocument()

  const deleteButton = screen.getByRole('button', { name: /delete qwen 3\.5 9b$/i })
  expect(deleteButton).toBeEnabled()
  fireEvent.click(deleteButton)
  fireEvent.click(screen.getByRole('button', { name: /delete model/i }))
  expect(deleteModel).toHaveBeenCalledWith('qwen3.5-9b-q4')
})

test('allows explicit Talk-incompatible models to run with an agent-readiness warning', () => {
  const loadModel = vi.fn()
  const deleteModel = vi.fn()
  useModelsMock.mockReturnValue(baseState({
    loadModel,
    deleteModel,
    models: [model({
      name: 'Phi-4 Mini',
      status: 'downloaded',
      contextLength: 128000,
      appCompatibility: {
        agentViability: {
          status: 'not_agent_viable',
          reason: 'Direct chat works, but ODS Talk failed validation.',
        },
        hermesTalk: {
          status: 'unsupported_until_revalidated',
          reason: 'Direct chat works, but ODS Talk failed validation.',
        },
      },
    })],
  }))

  renderModels()

  const runButton = screen.getByRole('button', { name: /^run$/i })
  expect(runButton).toBeEnabled()
  expect(runButton).toHaveAttribute('title', 'Run Phi-4 Mini')
  fireEvent.click(runButton)
  expect(loadModel).toHaveBeenCalledWith('qwen3.5-9b-q4')
  expect(screen.getByText('Direct chat only')).toBeInTheDocument()
  expect(screen.getByText('Agent blocked')).toBeInTheDocument()

  const deleteButton = screen.getByRole('button', { name: /delete phi-4 mini$/i })
  expect(deleteButton).toBeEnabled()
})

test('blocks direct-chat-incompatible models before Run', () => {
  const loadModel = vi.fn()
  const deleteModel = vi.fn()
  const reason = 'Fleet validation could not load this model into the local chat runtime.'
  useModelsMock.mockReturnValue(baseState({
    loadModel,
    deleteModel,
    models: [model({
      name: 'Phi-3.5 Mini',
      status: 'downloaded',
      contextLength: 128000,
      appCompatibility: {
        openaiChat: {
          status: 'unsupported_until_revalidated',
          reason,
        },
      },
    })],
  }))

  renderModels()

  const runButton = screen.getByRole('button', { name: /chat unsupported/i })
  expect(runButton).toBeDisabled()
  expect(runButton).toHaveAttribute('title', reason)
  fireEvent.click(runButton)
  expect(loadModel).not.toHaveBeenCalled()
  expect(screen.getByText('Unavailable')).toBeInTheDocument()
  expect(screen.getByText('Chat blocked')).toBeInTheDocument()

  const deleteButton = screen.getByRole('button', { name: /delete phi-3\.5 mini$/i })
  expect(deleteButton).toBeEnabled()
})

test('keeps Download available in cloud mode', () => {
  const downloadModel = vi.fn()
  useModelsMock.mockReturnValue(baseState({
    odsMode: 'cloud',
    configuredMode: 'cloud',
    canActivateModels: false,
    activationModeError: 'ODS is running in cloud mode. A local-mode installation is required to run downloaded models.',
    downloadModel,
    models: [model()],
  }))

  renderModels()
  const downloadButton = screen.getByRole('button', { name: /^download$/i })
  expect(downloadButton).toBeEnabled()
  fireEvent.click(downloadButton)

  expect(downloadModel).toHaveBeenCalledWith('qwen3.5-9b-q4')
  expect(screen.getByText('Runtime: Cloud')).toBeInTheDocument()
  expect(screen.getByText(/Model downloads and deletion remain available/i)).toBeInTheDocument()
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

test.each([
  [
    'single GGUF filename',
    { gguf: 'Qwen3.5-9B-Q4_K_M.gguf' },
    'Qwen3.5-9B-Q4_K_M.gguf',
  ],
  [
    'split-part progress label',
    {
      gguf: 'Qwen3-Coder-Next-Q4_K_M-00001-of-00002.gguf',
      ggufParts: [
        { file: 'Qwen3-Coder-Next-Q4_K_M-00001-of-00002.gguf' },
        { file: 'Qwen3-Coder-Next-Q4_K_M-00002-of-00002.gguf' },
      ],
    },
    'Qwen3-Coder-Next-Q4_K_M-00002-of-00002.gguf (part 2/2)',
  ],
])('retries a failed %s with the catalog model ID', async (_label, modelFields, progressModel) => {
  const downloadModel = vi.fn()
  useModelsMock.mockReturnValue(baseState({
    downloadModel,
    models: [model(modelFields)],
  }))
  useDownloadProgressMock.mockReturnValue(baseDownloadState({
    progress: {
      status: 'failed',
      model: progressModel,
      error: 'Transfer failed.',
    },
  }))

  renderModels()
  fireEvent.click(screen.getByRole('button', { name: /retry/i }))

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

test('shows cancellation state and errors without hiding active progress', () => {
  useModelsMock.mockReturnValue(baseState({ models: [model()] }))
  useDownloadProgressMock.mockReturnValue(baseDownloadState({
    isDownloading: true,
    isCancelling: true,
    cancelError: 'The host agent did not accept cancellation.',
    progress: {
      status: 'downloading',
      model: 'qwen3.5-9b-q4',
      bytesDownloaded: 5,
      bytesTotal: 10,
      percent: 50,
      speedMbps: 1,
      eta: 5,
    },
  }))

  renderModels()

  expect(screen.getByText(/downloading qwen3\.5-9b-q4/i)).toBeInTheDocument()
  expect(screen.getByRole('alert')).toHaveTextContent('The host agent did not accept cancellation.')
  expect(screen.getByRole('button', { name: /cancelling/i })).toBeDisabled()
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

test('does not expose Retry while the download start request is unresolved', async () => {
  vi.useFakeTimers()
  const startRequest = deferred()
  useModelsMock.mockReturnValue(baseState({
    downloadModel: vi.fn(() => startRequest.promise),
    models: [model()],
  }))

  try {
    renderModels()
    fireEvent.click(screen.getByRole('button', { name: /^download$/i }))

    await act(async () => { await vi.advanceTimersByTimeAsync(60000) })
    expect(screen.getByRole('button', { name: /starting/i })).toBeDisabled()
    expect(screen.queryByRole('button', { name: /retry/i })).not.toBeInTheDocument()

    await act(async () => {
      startRequest.reject(new Error('Download start timed out.'))
      await startRequest.promise.catch(() => {})
    })
    expect(screen.getByRole('button', { name: /retry/i })).toBeEnabled()
  } finally {
    vi.useRealTimers()
  }
})

test('does not let unrelated mutation errors clear an in-flight download start', () => {
  const startRequest = deferred()
  let hookState = baseState({
    downloadModel: vi.fn(() => startRequest.promise),
    models: [model()],
  })
  useModelsMock.mockImplementation(() => hookState)

  const view = renderModels()
  fireEvent.click(screen.getByRole('button', { name: /^download$/i }))
  expect(screen.getByRole('button', { name: /starting/i })).toBeDisabled()

  hookState = { ...hookState, error: 'Delete is blocked by the active runtime.' }
  view.rerender(createElement(MemoryRouter, null, createElement(Models)))

  expect(screen.getByText('Delete is blocked by the active runtime.')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /starting/i })).toBeDisabled()
  expect(screen.queryByRole('button', { name: /retry/i })).not.toBeInTheDocument()
})

test('keeps Delete visible but disabled while that model is working', () => {
  useModelsMock.mockReturnValue(baseState({
    actionLoading: 'qwen3.5-9b-q4',
    actionLoadingModels: ['qwen3.5-9b-q4'],
    models: [model({ status: 'downloaded' })],
  }))

  renderModels()

  expect(screen.getByRole('button', { name: /working/i })).toBeDisabled()
  const deleteButton = screen.getByRole('button', { name: /delete qwen 3\.5 9b$/i })
  expect(deleteButton).toBeDisabled()
  expect(deleteButton).toHaveAttribute('title', 'Wait for the current model action to finish before deleting it.')
})

test('locks every model action while activation is in progress, including rollback and downloads', () => {
  useModelsMock.mockReturnValue(baseState({
    actionLoading: 'next-model',
    actionLoadingModels: ['next-model'],
    activationLoading: 'next-model',
    models: [
      model({ id: 'rollback-model', name: 'Rollback Model', status: 'downloaded' }),
      model({ id: 'next-model', name: 'Next Model', status: 'downloaded' }),
      model({ id: 'available-model', name: 'Available Model', status: 'available' }),
    ],
  }))

  renderModels()

  expect(screen.getByText('Rollback Model')).toBeInTheDocument()
  for (const button of screen.getAllByRole('button', { name: /^run$/i })) {
    expect(button).toBeDisabled()
  }
  const rollbackDelete = screen.getByRole('button', { name: /delete rollback model$/i })
  expect(rollbackDelete).toBeDisabled()
  expect(rollbackDelete).toHaveAttribute('title', 'Wait for the current model swap to finish before deleting another model.')
  const targetDelete = screen.getByRole('button', { name: /delete next model$/i })
  expect(targetDelete).toBeDisabled()
  expect(targetDelete).toHaveAttribute('title', 'Wait for the current model action to finish before deleting it.')
  expect(screen.getByRole('button', { name: /^download$/i })).toBeDisabled()
})

test('keeps Run visible with the runtime-mode reason when activation is unavailable', () => {
  const loadModel = vi.fn()
  const activationModeError = 'ODS is running in cloud mode. A local-mode installation is required to run downloaded models.'
  useModelsMock.mockReturnValue(baseState({
    odsMode: 'cloud',
    configuredMode: 'cloud',
    canActivateModels: false,
    activationModeError,
    loadModel,
    models: [model({ status: 'downloaded' })],
  }))

  renderModels()

  const runButton = screen.getByRole('button', { name: /^run$/i })
  expect(runButton).toBeDisabled()
  expect(runButton).toHaveAttribute('title', activationModeError)
  expect(screen.getByRole('button', { name: /delete qwen 3\.5 9b$/i })).toBeEnabled()
  expect(screen.getByRole('link', { name: /review runtime settings/i })).toHaveAttribute('href', '/settings')
  expect(loadModel).not.toHaveBeenCalled()
})

test('keeps Run visible with the VRAM requirement when the model does not fit', () => {
  const loadModel = vi.fn()
  useModelsMock.mockReturnValue(baseState({
    loadModel,
    models: [model({ status: 'downloaded', fitsVram: false, vramRequired: 12 })],
  }))

  renderModels()

  const runButton = screen.getByRole('button', { name: /^run$/i })
  expect(runButton).toBeDisabled()
  expect(runButton).toHaveAttribute('title', 'Requires 12 GB VRAM; the detected GPU has 8.0 GB total.')
  fireEvent.click(runButton)
  expect(loadModel).not.toHaveBeenCalled()
})

test('shows effective and configured runtime modes when they differ', () => {
  useModelsMock.mockReturnValue(baseState({
    odsMode: 'local',
    configuredMode: 'cloud',
    canActivateModels: false,
    activationModeError: 'ODS is running in local mode but configured for cloud mode. Restart or repair ODS before running a local model.',
    models: [model({ status: 'downloaded' })],
  }))

  renderModels()

  expect(screen.getByText('Runtime: Local / configured Cloud')).toBeInTheDocument()
  expect(screen.getByText(/running in local mode but configured for cloud mode/i)).toBeInTheDocument()
})

test('treats currentModel as active even if a stale row still says downloaded', () => {
  useModelsMock.mockReturnValue(baseState({
    currentModel: 'qwen3.5-9b-q4',
    models: [model({ status: 'downloaded' })],
  }))

  renderModels()

  expect(screen.getByText('Active')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /benchmark/i })).toBeInTheDocument()
  const deleteButton = screen.getByRole('button', { name: /delete qwen 3\.5 9b unavailable/i })
  expect(deleteButton).toBeDisabled()
  expect(deleteButton).toHaveAttribute('title', 'The active model cannot be deleted. Run another model first.')
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
