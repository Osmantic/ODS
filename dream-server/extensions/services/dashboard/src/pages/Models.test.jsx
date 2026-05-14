import { createElement } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import Models from './Models'

const useModelsMock = vi.fn()

vi.mock('../hooks/useModels', () => ({
  useModels: () => useModelsMock(),
}))

vi.mock('../hooks/useDownloadProgress', () => ({
  useDownloadProgress: () => ({
    isDownloading: false,
    progress: null,
    refresh: vi.fn(),
    formatBytes: (value) => `${value} B`,
    formatEta: (value) => `${value}s`,
  }),
}))

function baseState(overrides = {}) {
  return {
    models: [],
    gpu: { vramUsed: 2, vramTotal: 8, vramFree: 6 },
    currentModel: null,
    loading: false,
    error: null,
    actionLoading: null,
    downloadModel: vi.fn(),
    loadModel: vi.fn(),
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

  render(createElement(Models))

  expect(screen.getByRole('button', { name: /model library/i })).toBeInTheDocument()
  expect(screen.getByText('VRAM')).toBeInTheDocument()
  expect(screen.getByText('Speed')).toBeInTheDocument()
  expect(screen.getByText('Currently running: qwen3.5-9b-q4')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /dashboard/i })).toHaveAttribute('href', '/')
  expect(screen.getByText('51.7 tok/s')).toBeInTheDocument()
  expect(screen.getByText('69.8 tok/s')).toBeInTheDocument()
  expect(screen.getByText('3.2 GB')).toBeInTheDocument()
})

test('loaded models show active state without benchmark actions', () => {
  useModelsMock.mockReturnValue(baseState({
    currentModel: 'qwen3.5-9b-q4',
    models: [model({ status: 'loaded' })],
  }))

  render(createElement(Models))

  expect(screen.getByText('Active')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /benchmark/i })).not.toBeInTheDocument()
})

test('runs downloaded models through the existing load action', () => {
  const loadModel = vi.fn()
  useModelsMock.mockReturnValue(baseState({
    loadModel,
    models: [model({ status: 'downloaded' })],
  }))

  render(createElement(Models))
  fireEvent.click(screen.getByRole('button', { name: /^run$/i }))

  expect(loadModel).toHaveBeenCalledWith('qwen3.5-9b-q4')
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

  render(createElement(Models))

  fireEvent.click(screen.getByRole('button', { name: /code1/i }))

  expect(screen.getByText('Qwen 3 Coder Next')).toBeInTheDocument()
  expect(screen.queryByText('Qwen 3.5 9B')).not.toBeInTheDocument()

  fireEvent.change(screen.getByPlaceholderText('Search models...'), { target: { value: '9B' } })

  expect(screen.getByText('No models match the current filters.')).toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: /reset/i }))
  expect(screen.getByText('Qwen 3.5 9B')).toBeInTheDocument()
})
