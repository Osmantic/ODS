import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import GPUMonitor from './GPUMonitor'

vi.mock('../hooks/useGPUDetailed', () => ({
  useGPUDetailed: vi.fn(() => ({
    detailed: null,
    history: [],
    topology: null,
    loading: false,
    error: null,
  })),
}))

import { useGPUDetailed } from '../hooks/useGPUDetailed'

function renderRoute(status, loading) {
  return render(
    <MemoryRouter initialEntries={['/gpu']}>
      <Routes>
        <Route path="/gpu" element={<GPUMonitor status={status} loading={loading} />} />
        <Route path="/dashboard" element={<div>Dashboard Route</div>} />
      </Routes>
    </MemoryRouter>
  )
}

describe('GPUMonitor cloud/loading gate', () => {
  beforeEach(() => {
    useGPUDetailed.mockClear()
  })

  it('does not mount local hook while status is loading', () => {
    renderRoute(undefined, true)
    expect(useGPUDetailed).not.toHaveBeenCalled()
    expect(screen.getByText(/Loading GPU status/)).toBeInTheDocument()
  })

  it('does not mount local hook for cloud inference and redirects', async () => {
    const cloudStatus = {
      inferenceMode: 'cloud',
      inferenceSource: 'cloud-mode',
      gpu: { gpu_count: 2 },
    }
    renderRoute(cloudStatus, false)
    expect(useGPUDetailed).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.getByText('Dashboard Route')).toBeInTheDocument())
  })

  it('does not mount local hook for remote inference and redirects', async () => {
    const remoteStatus = {
      inferenceMode: 'remote',
      inferenceSource: 'remote-provider',
      gpu: { gpu_count: 2 },
    }
    renderRoute(remoteStatus, false)
    expect(useGPUDetailed).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.getByText('Dashboard Route')).toBeInTheDocument())
  })

  it('mounts local hook for local inference', () => {
    const localStatus = {
      inferenceMode: 'local',
      inferenceSource: 'local-runtime',
      gpu: { gpu_count: 2 },
    }
    renderRoute(localStatus, false)
    expect(useGPUDetailed).toHaveBeenCalled()
  })
})
