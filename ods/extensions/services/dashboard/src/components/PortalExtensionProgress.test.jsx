import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
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

const githubCommand = '/extensions install https://github.com/owner/demo'
const failedInstallation = {command: githubCommand, target: 'demo', state: 'failed', chatId: 'chat', requestId: 'request'}

test.each(['failed', 'reconciliation_required'])('rechecks %s once per target phase without inferring authoritative success', async state => {
  vi.useFakeTimers()
  let currentPlan = plan('blocked', 'error')
  const fetcher = vi.fn().mockImplementation(async url => response(url.endsWith('/projects')
    ? {extensionId: 'demo', scope: 'project-association', projects: ['Playground/project']}
    : currentPlan))
  vi.stubGlobal('fetch', fetcher)
  const recheck = vi.fn(), catalogResume = vi.fn()
  const props = {command: githubCommand, active: true, projectPath: 'Playground/project',
    installation: {...failedInstallation, state}, onRecheckGithubObservation: recheck,
    onRecheckInstallation: catalogResume}
  const view = render(<PortalExtensionProgress {...props}/>)
  await act(async () => { await vi.advanceTimersByTimeAsync(0) })
  expect(recheck).not.toHaveBeenCalled()
  currentPlan = plan()
  await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
  expect(screen.getByText('Installing')).toBeInTheDocument()
  expect(recheck).toHaveBeenCalledTimes(1)
  view.rerender(<PortalExtensionProgress {...props} installation={{...props.installation}}/>)
  await act(async () => { await vi.advanceTimersByTimeAsync(15000) })
  expect(recheck).toHaveBeenCalledTimes(1)
  currentPlan = plan('none', 'cli_installed')
  await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
  expect(screen.getByText('Ready')).toBeInTheDocument()
  expect(recheck).toHaveBeenCalledTimes(2)
  await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
  expect(recheck).toHaveBeenCalledTimes(2)
  expect(catalogResume).not.toHaveBeenCalled()
  expect(fetcher.mock.calls.every(([, options]) => options.method === undefined)).toBe(true)
  expect(screen.queryByText(/Linked to/)).not.toBeInTheDocument()
  view.rerender(<PortalExtensionProgress {...props} active={false}
    installation={{...props.installation, state: 'succeeded'}}/>)
  await act(async () => { await vi.advanceTimersByTimeAsync(0) })
  expect(screen.queryByText(/this installation attempt failed/)).not.toBeInTheDocument()
  expect(screen.queryByText(/Installation needs inspection/)).not.toBeInTheDocument()
  expect(screen.getByText('Linked to Playground/project')).toBeInTheDocument()
  expect(fetcher.mock.calls.filter(([, options]) => options.method === 'POST')).toHaveLength(1)
})

test('dependency progress does not resume observation for a failed target', async () => {
  vi.useFakeTimers()
  const next = plan('blocked', 'error')
  next.steps.unshift({extensionId: 'dependency', action: 'wait', status: 'installing', missingConfiguration: []})
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(next)))
  const recheck = vi.fn()
  render(<PortalExtensionProgress command={githubCommand} installation={failedInstallation}
    onRecheckGithubObservation={recheck}/>)
  await act(async () => { await vi.advanceTimersByTimeAsync(15000) })
  expect(recheck).not.toHaveBeenCalled()
  expect(screen.getByText(/this installation attempt failed/)).toBeInTheDocument()
})

test.each([plan('none', 'not_installed'), plan('wait', 'error'), {...plan(), extensionId: 'other'}])(
  'invalid plan does not trigger authoritative observation: %j', async invalid => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(invalid)))
    const recheck = vi.fn()
    render(<PortalExtensionProgress command={githubCommand} installation={failedInstallation}
      onRecheckGithubObservation={recheck}/>)
    expect(await screen.findByText('Current installation status could not be confirmed.')).toBeInTheDocument()
    expect(recheck).not.toHaveBeenCalled()
  })

test('dedupe resets for a new request, not a callback rerender or manual refresh', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(plan('none', 'cli_installed'))))
  const recheck = vi.fn()
  const props = {command: githubCommand, installation: failedInstallation, onRecheckGithubObservation: recheck}
  const view = render(<PortalExtensionProgress {...props}/>)
  await waitFor(() => expect(recheck).toHaveBeenCalledTimes(1))
  view.rerender(<PortalExtensionProgress {...props} onRecheckGithubObservation={() => recheck()}/>)
  expect(recheck).toHaveBeenCalledTimes(1)
  fireEvent.click(screen.getByRole('button', {name: 'Refresh status'}))
  await screen.findByText('Ready')
  expect(recheck).toHaveBeenCalledTimes(2)
  view.rerender(<PortalExtensionProgress {...props} installation={{...failedInstallation, requestId: 'next'}}/>)
  await waitFor(() => expect(recheck).toHaveBeenCalledTimes(3))
})

test('catalog progress and Refresh never automatically call catalog resume', async () => {
  const fetcher = vi.fn().mockResolvedValue(response(plan('none', 'enabled')))
  vi.stubGlobal('fetch', fetcher)
  const resume = vi.fn()
  render(<PortalExtensionProgress command="/extensions @demo" onRecheckInstallation={resume}
    installation={{...failedInstallation, command: '/extensions @demo', state: 'reconciliation_required'}}/>)
  await screen.findByText('Ready')
  fireEvent.click(screen.getByRole('button', {name: 'Refresh status'}))
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2))
  expect(resume).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', {name: 'Recheck installation'}))
  expect(resume).toHaveBeenCalledTimes(1)
})

test.each([{requestId: undefined}, {chatId: undefined}, {state: 'pending'}, {state: 'succeeded'}])(
  'automatic recheck requires a failed scoped request: %j', async change => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(plan('none', 'cli_installed'))))
    const recheck = vi.fn()
    render(<PortalExtensionProgress command={githubCommand} installation={{...failedInstallation, ...change}}
      onRecheckGithubObservation={recheck}/>)
    await screen.findByText('Ready')
    expect(recheck).not.toHaveBeenCalled()
  })
