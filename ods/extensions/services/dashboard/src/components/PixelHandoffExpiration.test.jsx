import { act, fireEvent, render, screen } from '@testing-library/react'
import PixelHandoffApproval from './PixelHandoffApproval'

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); localStorage.clear() })

test('expires visible consent locally while a status refresh is stalled', async () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-09-11T00:00:00Z'))
  const runId = '5c292c25-9368-4d9a-83cd-1e14d34cb128'
  const recipient = { kind: 'local', scope: 'run', label: 'Local provider' }
  const checkpointJson = JSON.stringify({ schemaVersion: 1, runId, agentId: 'pixel',
    dataScope: 'conversation-and-this-run-tool-results', returnAction: 'configured-leader-on-next-run', messages: [], recipient })
  localStorage.setItem('ods.pixel.handoff.run.v1', runId)
  let reads = 0
  const fetchMock = vi.fn(async url => {
    if (url.endsWith('/list')) {
      if (reads++) return new Promise(() => {})
      return { ok: true, json: async () => ({ items: [], unavailableCount: 0 }) }
    }
    return { ok: true, json: async () => ({ runId, recipient, checkpointJson,
      checkpointDigest: 'a'.repeat(64), status: 'pending', expiresAt: Date.now() / 1000 + 3 }) }
  })
  vi.stubGlobal('fetch', fetchMock)
  render(<PixelHandoffApproval />)
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Review handoffs' })))
  const consent = screen.getByLabelText('I reviewed this recipient and checkpoint and approve the stated run scope')
  fireEvent.click(consent)
  expect(screen.getByRole('button', { name: 'Approve this run' })).toBeEnabled()
  await act(async () => vi.advanceTimersByTimeAsync(3000))
  expect(screen.getByRole('button', { name: 'Approve this run' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Decline handoff' })).toBeDisabled()
  expect(consent).not.toBeChecked()
  expect(screen.getByRole('status')).toHaveTextContent('approval window has expired')
  expect(fetchMock.mock.calls.every(([url]) => !url.endsWith('/decide'))).toBe(true)
})
