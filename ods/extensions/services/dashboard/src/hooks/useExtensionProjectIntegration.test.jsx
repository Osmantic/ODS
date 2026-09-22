import { StrictMode } from 'react'
import { act, renderHook } from '@testing-library/react'
import useExtensionProjectIntegration, { integrationRequest } from './useExtensionProjectIntegration'
import { readIntegrationRecovery, integrationRequestId } from '../lib/extensionIntegrationRecovery'

beforeEach(() => localStorage.clear())
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

const command = '/extensions @demo use it in this project'
const installed = {state: 'succeeded', command, target: 'demo', chatId: 'chat', requestId: 'turn'}
const props = () => ({chatId: 'chat', installation: installed, command, project: 'Playground/app', idle: true, sendMessage: vi.fn()})

test('continues through the real sender once after readiness without replaying the slash command', () => {
  const input = props()
  const view = renderHook(value => useExtensionProjectIntegration(value), {initialProps: {...input, idle: false}, wrapper: StrictMode})
  expect(input.sendMessage).not.toHaveBeenCalled()
  view.rerender(input)
  expect(input.sendMessage).toHaveBeenCalledTimes(1)
  const prompt = input.sendMessage.mock.calls[0][0]
  expect(prompt).toContain('@demo into Playground/app')
  expect(prompt).toContain('project integration is still pending')
  expect(prompt).not.toContain('/extensions')
  expect(input.sendMessage.mock.calls[0][1]).toMatch(/^[a-f0-9]{64}$/)
  view.rerender({...input, installation: {...installed}})
  expect(input.sendMessage).toHaveBeenCalledTimes(1)
})

test.each([undefined, {...installed, state: 'pending'}, {...installed, state: 'blocked'},
  {...installed, chatId: 'old-chat'}, {...installed, requestId: undefined}, {...installed, command: 'other request'}])(
  'history, unfinished operations and unrelated receipts cannot trigger code changes: %j', installation => {
    const input = {...props(), installation}
    renderHook(() => useExtensionProjectIntegration(input))
    expect(input.sendMessage).not.toHaveBeenCalled()
  })

test('a chat switch cannot reuse the previous chat installation receipt', () => {
  const input = props()
  const view = renderHook(value => useExtensionProjectIntegration(value), {initialProps: {...input, idle: false}})
  view.rerender({...input, chatId: 'new-chat'})
  expect(input.sendMessage).not.toHaveBeenCalled()
})

test('an explicit different project requires resolution before integration', () => {
  const other = '/extensions @demo use Playground/other'
  expect(integrationRequest({...installed, command: other}, other, 'Playground/app', 'chat')).toBeNull()
  expect(integrationRequest(installed, command, '../app', 'chat')).toBeNull()
})

test('reload offers read-only readiness recovery without automatically sending or installing', async () => {
  const input = props()
  const first = renderHook(value => useExtensionProjectIntegration(value), {initialProps: {...input, idle: false,
    installation: {...installed, state: 'pending'}}})
  first.unmount()
  expect(readIntegrationRecovery('chat').phase).toBe('pending')
  const fetcher = vi.fn().mockResolvedValue({ok: true, json: async () => ({schemaVersion: 1, extensionId: 'demo',
    steps: [{extensionId: 'demo', action: 'none', status: 'enabled', missingConfiguration: []}]})})
  vi.stubGlobal('fetch', fetcher)
  const restored = renderHook(() => useExtensionProjectIntegration({...input, installation: null}))
  expect(restored.result.current.recovery.target).toBe('demo')
  expect(input.sendMessage).not.toHaveBeenCalled()
  expect(fetcher).not.toHaveBeenCalled()
  await act(async () => { await restored.result.current.resume() })
  expect(fetcher).toHaveBeenCalledTimes(1)
  expect(fetcher.mock.calls[0][0]).toBe('/api/extensions/demo/install-plan')
  expect(fetcher.mock.calls[0][1].method).toBeUndefined()
  expect(input.sendMessage).toHaveBeenCalledTimes(1)
  expect(readIntegrationRecovery('chat').phase).toBe('dispatched')
  restored.unmount()
  renderHook(() => useExtensionProjectIntegration(input))
  expect(input.sendMessage).toHaveBeenCalledTimes(1)
})

test('a failed readiness check leaves the saved work pending without a mutation', async () => {
  const input = props()
  const first = renderHook(() => useExtensionProjectIntegration({...input, idle: false}))
  first.unmount()
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ok: false}))
  const restored = renderHook(() => useExtensionProjectIntegration({...input, installation: null}))
  await act(async () => { await restored.result.current.resume() })
  expect(restored.result.current.recovery.error).toContain('could not be confirmed')
  expect(input.sendMessage).not.toHaveBeenCalled()
  expect(readIntegrationRecovery('chat').phase).toBe('pending')
})

test('the same original request has a stable model request identity across tabs', () => {
  const record = {...installed, project: 'Playground/app'}
  expect(integrationRequestId(record)).toBe(integrationRequestId({...record}))
  expect(integrationRequestId(record)).not.toBe(integrationRequestId({...record, project: 'Playground/other'}))
  expect(integrationRequestId(record)).not.toBe(integrationRequestId({...record, requestId: 'new'}))
})

test('storage failure never sends a continuation without its recovery record', () => {
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota') })
  const input = props()
  const view = renderHook(() => useExtensionProjectIntegration(input))
  expect(input.sendMessage).not.toHaveBeenCalled()
  expect(view.result.current.recovery.error).toContain('No request was sent')
})
