import { act, fireEvent, screen, within } from '@testing-library/react'
import { render } from '../test/test-utils'
import Extensions from './Extensions'

const json = (body, status = 200) => ({ ok: status < 400, status, json: async () => body })
async function openConsole(readLogs, progressStatus = 'idle') {
  vi.stubGlobal('fetch', vi.fn(async url => {
    if (url === '/api/extensions/catalog') return json({ agent_available: true, extensions: [{
      id: 'gitea', name: 'Gitea', status: 'enabled', source: 'user', features: [],
    }] })
    if (url === '/api/templates') return json({ templates: [] })
    if (url === '/api/extensions/gitea/progress') return json({ status: progressStatus })
    if (url === '/api/extensions/gitea/logs') return readLogs()
    throw new Error(`Unexpected request: ${url}`)
  }))
  render(<Extensions compact />)
  await screen.findByText('Gitea')
  vi.useFakeTimers()
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Logs' })))
  return within(screen.getByRole('dialog', { name: 'Gitea logs' }))
}
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

test('serializes automatic and manual log reads and resumes polling after a slow refresh', async () => {
  let first, manual
  const readLogs = vi.fn()
    .mockImplementationOnce(() => new Promise(resolve => { first = resolve }))
    .mockImplementationOnce(() => new Promise(resolve => { manual = resolve }))
    .mockResolvedValue(json({ logs: 'Latest automatic snapshot' }))
  const consoleView = await openConsole(readLogs)
  const refresh = consoleView.getByTitle('Refresh now')
  fireEvent.click(refresh)
  expect(readLogs).toHaveBeenCalledTimes(1)
  expect(refresh).toBeDisabled()

  await act(async () => first(json({ logs: 'Initial snapshot' })))
  expect(consoleView.getByText('Initial snapshot')).toBeInTheDocument()
  expect(refresh).toBeEnabled()
  await act(async () => fireEvent.click(refresh))
  fireEvent.click(refresh)
  expect(readLogs).toHaveBeenCalledTimes(2)
  expect(refresh).toBeDisabled()
  await act(async () => vi.advanceTimersByTimeAsync(10000))
  expect(readLogs).toHaveBeenCalledTimes(2)

  await act(async () => manual(json({ logs: 'Fresh manual snapshot' })))
  expect(consoleView.getByText('Fresh manual snapshot')).toBeInTheDocument()
  expect(refresh).toBeEnabled()
  await act(async () => vi.advanceTimersByTimeAsync(2000))
  expect(readLogs).toHaveBeenCalledTimes(3)
  expect(consoleView.getByText('Latest automatic snapshot')).toBeInTheDocument()
})

test('releases the read gate after both automatic and manual failures', async () => {
  const readLogs = vi.fn()
    .mockRejectedValueOnce(new TypeError('Network unavailable'))
    .mockResolvedValueOnce(json({ detail: 'Host agent unavailable' }, 503))
    .mockResolvedValue(json({ logs: 'Recovered logs' }))
  const consoleView = await openConsole(readLogs)
  const refresh = consoleView.getByTitle('Refresh now')
  expect(consoleView.getByText('Network unavailable')).toBeInTheDocument()
  expect(refresh).toBeEnabled()
  await act(async () => fireEvent.click(refresh))
  expect(consoleView.getByText('Host agent unavailable')).toBeInTheDocument()
  expect(refresh).toBeEnabled()
  await act(async () => vi.advanceTimersByTimeAsync(2000))
  expect(readLogs).toHaveBeenCalledTimes(3)
  expect(consoleView.getByText('Recovered logs')).toBeInTheDocument()
  expect(consoleView.queryByText('Host agent unavailable')).not.toBeInTheDocument()
})

test('stops automatic log polling after terminal install progress', async () => {
  const readLogs = vi.fn().mockResolvedValue(json({ logs: 'Terminal snapshot' }))
  const consoleView = await openConsole(readLogs, 'started')
  expect(consoleView.getByText('Terminal snapshot')).toBeInTheDocument()
  await act(async () => { await vi.advanceTimersByTimeAsync(6000) })
  expect(readLogs).toHaveBeenCalledTimes(1)
})
