import { act, render, screen } from '@testing-library/react'
import { PreFlightChecks } from '../PreFlightChecks'

describe('PreFlightChecks', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn((url) => {
      const payloads = {
        '/api/preflight/required-ports': { ports: [{ port: 3000, service: 'Open WebUI' }] },
        '/api/preflight/docker': { available: true, version: '27.0' },
        '/api/preflight/gpu': { available: true, name: 'Test GPU', vram: 8 },
        '/api/preflight/ports': {
          conflicts: [{ port: 3000, service: 'Open WebUI', in_use: true }],
        },
        '/api/preflight/disk': { free: 100e9 },
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(payloads[url]),
      })
    }))
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  test('reports an occupied requested port instead of assuming ODS owns it', async () => {
    render(<PreFlightChecks />)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2300)
    })

    expect(screen.getByText('1 port(s) in use')).toBeInTheDocument()
    expect(screen.getByText('Port 3000 (Open WebUI)')).toBeInTheDocument()
    expect(screen.queryByText('1 services already running')).not.toBeInTheDocument()
  })
})
