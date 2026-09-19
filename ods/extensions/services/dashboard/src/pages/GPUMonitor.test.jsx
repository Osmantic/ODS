import { fireEvent, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from '../test/test-utils'
import { useGPUDetailed } from '../hooks/useGPUDetailed'
import { downloadGpuHistoryCsv } from '../utils/gpuHistoryCsv'
import GPUMonitor from './GPUMonitor' // eslint-disable-line no-unused-vars

vi.mock('../hooks/useGPUDetailed', () => ({ useGPUDetailed: vi.fn() }))
vi.mock('../utils/gpuHistoryCsv', () => ({ downloadGpuHistoryCsv: vi.fn() }))

const history = {
  timestamps: ['2026-09-04T00:00:00Z'],
  gpus: { 0: { utilization: [42] } },
}

describe('GPU monitor history export', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useGPUDetailed.mockReturnValue({
      detailed: {
        gpus: [{ index: 0, uuid: 'gpu-0', name: 'Test GPU', assigned_services: [] }],
        backend: 'nvidia',
        gpu_count: 1,
      },
      history,
      topology: null,
      loading: false,
      error: null,
    })
  })

  it('exports the currently displayed history from the public History tab', () => {
    render(<GPUMonitor />)

    fireEvent.click(screen.getByRole('button', { name: 'History' }))
    fireEvent.click(screen.getByRole('button', { name: 'Export CSV' }))

    expect(downloadGpuHistoryCsv).toHaveBeenCalledOnce()
    expect(downloadGpuHistoryCsv).toHaveBeenCalledWith(history)
  })

  it('disables export until at least one timestamp has been collected', () => {
    useGPUDetailed.mockReturnValue({
      detailed: { gpus: [], backend: 'cpu', gpu_count: 0 },
      history: { timestamps: [], gpus: {} },
      topology: null,
      loading: false,
      error: null,
    })
    render(<GPUMonitor />)

    fireEvent.click(screen.getByRole('button', { name: 'History' }))

    expect(screen.getByRole('button', { name: 'Export CSV' })).toBeDisabled()
  })
})
