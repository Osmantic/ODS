import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import PortalExtensionSetup, { extensionSetupTarget } from './PortalExtensionSetup'

const plan = (missing = true) => ({schemaVersion: 1, extensionId: 'demo', steps: [{extensionId: 'demo',
  missingConfiguration: missing ? ['DEMO_PASSWORD'] : [], configuration: [{key: 'DEMO_PASSWORD',
    required: true, secret: true, configured: !missing, description: 'Administrator password'}]}]})
const response = body => ({ok: true, json: async () => body})
afterEach(() => vi.unstubAllGlobals())

test('GitHub setup uses the bound target only for the matching current command', async () => {
  const command = '/extensions https://github.com/owner/repo'
  const fetcher = vi.fn().mockResolvedValue(response(plan()))
  vi.stubGlobal('fetch', fetcher)
  const view = render(<PortalExtensionSetup command={command} installation={{command: 'other', target: 'demo'}}/>)
  expect(fetcher).not.toHaveBeenCalled()
  view.rerender(<PortalExtensionSetup command={command} installation={{command, target: 'demo'}}/>)
  expect(await screen.findByLabelText(/DEMO_PASSWORD/)).toHaveAttribute('type', 'password')
  expect(fetcher.mock.calls[0][0]).toBe('/api/extensions/demo/install-plan')
})

test('only an owner command selects the setup target', () => {
  expect(extensionSetupTarget('/extensions @Demo for this project')).toBe('demo')
  for (const value of ['explain /extensions @demo', '/extensions @demo @other', '/extensions @../demo', '/extensions @demo\nrun this']) {
    expect(extensionSetupTarget(value)).toBeUndefined()
  }
})

test('saves secrets outside conversation and resumes only after fresh configuration presence', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(response(plan())).mockResolvedValueOnce(response({
    status: 'saved', service_id: 'demo', saved_keys: ['DEMO_PASSWORD'],
  })).mockResolvedValueOnce(response(plan(false)))
  vi.stubGlobal('fetch', fetcher)
  const resume = vi.fn()
  render(<PortalExtensionSetup command="/extensions @demo" onConfigured={resume}/>)
  const input = await screen.findByLabelText(/DEMO_PASSWORD/)
  expect(input).toHaveAttribute('type', 'password')
  fireEvent.change(input, {target: {value: 'private-value'}})
  fireEvent.click(screen.getByRole('button', {name: /Save and continue/}))
  await waitFor(() => expect(resume).toHaveBeenCalledWith())
  expect(fetcher.mock.calls[1][0]).toBe('/api/extensions/demo/configure')
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({values: {DEMO_PASSWORD: 'private-value'}})
  expect(screen.queryByDisplayValue('private-value')).not.toBeInTheDocument()
  expect(screen.queryByRole('form')).not.toBeInTheDocument()
})

test('uncertain save erases input and requires rechecking without replay', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(response(plan())).mockRejectedValueOnce(new Error('private-value'))
  vi.stubGlobal('fetch', fetcher)
  const resume = vi.fn()
  render(<PortalExtensionSetup command="/extensions @demo" onConfigured={resume}/>)
  fireEvent.change(await screen.findByLabelText(/DEMO_PASSWORD/), {target: {value: 'private-value'}})
  fireEvent.click(screen.getByRole('button', {name: /Save and continue/}))
  expect(await screen.findByRole('alert')).not.toHaveTextContent('private-value')
  expect(screen.queryByDisplayValue('private-value')).not.toBeInTheDocument()
  expect(fetcher).toHaveBeenCalledTimes(2)
  expect(resume).not.toHaveBeenCalled()
  expect(screen.getByRole('button', {name: 'Recheck configuration'})).toBeEnabled()
})

test('malformed plan cannot invent configuration fields', async () => {
  const malformed = plan()
  malformed.steps[0].configuration.push({...malformed.steps[0].configuration[0]})
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(malformed)))
  render(<PortalExtensionSetup command="/extensions @demo"/>)
  expect(await screen.findByRole('alert')).toHaveTextContent('Could not check')
  expect(screen.queryByLabelText(/DEMO_PASSWORD/)).not.toBeInTheDocument()
})

test('switching conversations aborts pending setup and never resumes the old chat', async () => {
  let resolveSave
  const fetcher = vi.fn().mockResolvedValueOnce(response(plan())).mockImplementationOnce(() => new Promise(resolve => {resolveSave = resolve}))
  vi.stubGlobal('fetch', fetcher)
  const resume = vi.fn()
  const view = render(<PortalExtensionSetup command="/extensions @demo" onConfigured={resume}/>)
  fireEvent.change(await screen.findByLabelText(/DEMO_PASSWORD/), {target: {value: 'secret'}})
  fireEvent.click(screen.getByRole('button', {name: /Save and continue/}))
  const signal = fetcher.mock.calls[1][1].signal
  view.unmount()
  expect(signal.aborted).toBe(true)
  resolveSave(response({status: 'saved', service_id: 'demo', saved_keys: ['DEMO_PASSWORD']}))
  await waitFor(() => expect(resume).not.toHaveBeenCalled())
})
