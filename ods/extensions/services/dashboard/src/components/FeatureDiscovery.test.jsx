import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { render } from '../test/test-utils'
import { FeatureGrid } from './FeatureDiscovery'

afterEach(() => vi.restoreAllMocks())

const featureData = {
  features: [{
    id: 'chat', icon: 'MessageSquare', name: 'Chat', description: 'Chat feature',
    status: 'available', enabled: false, requirements: { vramGb: 0, vramOk: true },
  }],
  recommendations: [],
}

it('shows a retryable error when feature instructions fail to load', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce({ ok: true, json: async () => featureData })
    .mockResolvedValueOnce({ ok: false, status: 503 })
    .mockResolvedValueOnce({ ok: true, json: async () => ({ name: 'Chat', instructions: { steps: ['Start'] } }) })
  vi.stubGlobal('fetch', fetchMock)
  render(<FeatureGrid />)

  await waitFor(() => expect(screen.getByRole('button', { name: /Chat/ })).toBeVisible())
  fireEvent.click(screen.getByRole('button', { name: /Chat/ }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Unable to load feature instructions (503)')
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  await waitFor(() => expect(screen.getByText('Start')).toBeVisible())
})
