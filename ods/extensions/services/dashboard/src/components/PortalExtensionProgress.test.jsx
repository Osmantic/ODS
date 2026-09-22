import { act, render, screen, waitFor } from '@testing-library/react'
import PortalExtensionProgress from './PortalExtensionProgress'

const plan = (action = 'wait', status = 'installing') => ({schemaVersion: 1, extensionId: 'demo', steps: [
  {extensionId: 'demo', action, status, missingConfiguration: []},
]})
const response = data => ({ok: true, json: async () => data})
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

test('GitHub progress follows the prepared target without guessing from repository name', async () => {
  const command = '/extensions https://github.com/owner/different-repo-name'
  const fetcher = vi.fn().mockResolvedValue(response(plan('none', 'enabled')))
  vi.stubGlobal('fetch', fetcher)
  render(<PortalExtensionProgress command={command} installation={{command, target: 'demo', state: 'succeeded'}}/>)
  expect(await screen.findByText('1/1 ready')).toBeInTheDocument()
  expect(fetcher.mock.calls[0][0]).toBe('/api/extensions/demo/install-plan')
})

test('a download remains observable after model reply ends without submitting mutations', async () => {
  vi.useFakeTimers()
  const fetcher = vi.fn().mockResolvedValueOnce(response(plan())).mockResolvedValue(response(plan('none', 'enabled')))
  vi.stubGlobal('fetch', fetcher)
  render(<PortalExtensionProgress command="/extensions @demo"/>)
  await act(async () => { await vi.advanceTimersByTimeAsync(0) })
  expect(screen.getByText('Installing')).toBeInTheDocument()
  await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
  expect(screen.getByText('1/1 ready')).toBeInTheDocument()
  await act(async () => { await vi.advanceTimersByTimeAsync(15000) })
  expect(fetcher).toHaveBeenCalledTimes(2)
  for (const [, options] of fetcher.mock.calls) expect(options.method).toBeUndefined()
})

test('unmount cancels observation rather than leaving a background poll', async () => {
  vi.useFakeTimers()
  const fetcher = vi.fn().mockResolvedValue(response(plan()))
  vi.stubGlobal('fetch', fetcher)
  const view = render(<PortalExtensionProgress command="/extensions @demo" active/>)
  await act(async () => { await vi.advanceTimersByTimeAsync(0) })
  const signal = fetcher.mock.calls[0][1].signal
  view.unmount()
  expect(signal.aborted).toBe(true)
  await act(async () => { await vi.advanceTimersByTimeAsync(20000) })
  expect(fetcher).toHaveBeenCalledTimes(1)
})

test('an inconsistent readiness response cannot display success', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(plan('none', 'not_installed'))))
  render(<PortalExtensionProgress command="/extensions @demo"/>)
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('could not be confirmed'))
  expect(screen.queryByText('1/1 ready')).not.toBeInTheDocument()
})

test('ordinary discussion never polls the installation API', () => {
  const fetcher = vi.fn()
  vi.stubGlobal('fetch', fetcher)
  render(<PortalExtensionProgress command="Explain /extensions @demo" active/>)
  expect(fetcher).not.toHaveBeenCalled()
})

test('links an observed project only after extension readiness is confirmed', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(response(plan('none', 'enabled'))).mockResolvedValueOnce(response({
    extensionId: 'demo', scope: 'project-association', projects: ['Playground/project'],
  }))
  vi.stubGlobal('fetch', fetcher)
  render(<PortalExtensionProgress command="/extensions @demo" projectPath="Playground/project"
    installation={{command: '/extensions @demo', target: 'demo', state: 'succeeded'}}/>)
  expect(await screen.findByText('Linked to Playground/project')).toBeInTheDocument()
  expect(fetcher.mock.calls[1][0]).toBe('/api/extensions/demo/projects')
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({project: 'Playground/project'})
})

test('does not link the old project when the owner names a different one', async () => {
  const fetcher = vi.fn().mockResolvedValue(response(plan('none', 'enabled')))
  vi.stubGlobal('fetch', fetcher)
  render(<PortalExtensionProgress command="/extensions @demo for Playground/new" projectPath="Playground/old"/>)
  expect(await screen.findByText('1/1 ready')).toBeInTheDocument()
  expect(fetcher).toHaveBeenCalledTimes(1)
  expect(screen.queryByText(/Linked to/)).not.toBeInTheDocument()
})

test.each([undefined, {command: '/extensions @demo for another task', target: 'demo', state: 'succeeded'},
  {command: '/extensions @demo', target: 'demo', state: 'pending'}])(
  'history and unrelated installation receipts cannot associate a project: %j', async installation => {
    const fetcher = vi.fn().mockResolvedValue(response(plan('none', 'enabled')))
    vi.stubGlobal('fetch', fetcher)
    render(<PortalExtensionProgress command="/extensions @demo" projectPath="Playground/project" installation={installation}/>)
    expect(await screen.findByText('1/1 ready')).toBeInTheDocument()
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(fetcher.mock.calls[0][1].method).toBeUndefined()
  })
