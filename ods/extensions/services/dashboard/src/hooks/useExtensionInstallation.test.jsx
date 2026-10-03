import { act, renderHook } from '@testing-library/react'
import useExtensionInstallation, { advanceCatalogInstallation } from './useExtensionInstallation'

const receipt = (state, overrides = {}) => ({schemaVersion: 1, extensionId: 'demo', state, dispatched: state === 'pending',
  plan: {extensionId: 'demo', steps: [{extensionId: 'demo', action: state === 'succeeded' ? 'none' : 'install',
    status: state === 'succeeded' ? 'enabled' : 'not_installed'}]}, ...overrides})
const response = value => ({ok: true, json: async () => value})
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

test('advances server-owned dependency steps sequentially until verified readiness', async () => {
  vi.useFakeTimers()
  const fetcher = vi.fn().mockResolvedValueOnce(response(receipt('pending'))).mockResolvedValueOnce(response(receipt('succeeded')))
  const report = vi.fn()
  const run = advanceCatalogInstallation('demo', new AbortController().signal, report, fetcher)
  await vi.advanceTimersByTimeAsync(0)
  expect(fetcher).toHaveBeenCalledTimes(1)
  await vi.advanceTimersByTimeAsync(5000)
  await run
  expect(report).toHaveBeenLastCalledWith({target: 'demo', state: 'succeeded'})
  for (const [url, options] of fetcher.mock.calls) {
    expect(url).toBe('/api/extensions/demo/install-next')
    expect(options.method).toBe('POST')
  }
})

test.each(['configuration_required', 'failed', 'blocked', 'reconciliation_required'])('does not advance past %s', async state => {
  const fetcher = vi.fn().mockResolvedValue(response(receipt(state)))
  await advanceCatalogInstallation('demo', new AbortController().signal, vi.fn(), fetcher)
  expect(fetcher).toHaveBeenCalledTimes(1)
})

test('a lost acknowledgement is not retried and inconsistent readiness is rejected', async () => {
  const fetcher = vi.fn().mockRejectedValue(new Error('connection lost'))
  await expect(advanceCatalogInstallation('demo', new AbortController().signal, vi.fn(), fetcher)).rejects.toThrow()
  expect(fetcher).toHaveBeenCalledTimes(1)
  fetcher.mockResolvedValue(response(receipt('succeeded', {dispatched: true})))
  await expect(advanceCatalogInstallation('demo', new AbortController().signal, vi.fn(), fetcher)).rejects.toThrow('inconsistent')
})

test('stopping during a pending step prevents subsequent dependency requests', async () => {
  vi.useFakeTimers()
  const controller = new AbortController()
  const fetcher = vi.fn().mockResolvedValue(response(receipt('pending')))
  const run = advanceCatalogInstallation('demo', controller.signal, vi.fn(), fetcher)
  await vi.advanceTimersByTimeAsync(0)
  controller.abort()
  await run
  await vi.advanceTimersByTimeAsync(10000)
  expect(fetcher).toHaveBeenCalledTimes(1)
})

test('rendering history never installs; only explicit command starts do, and changing chat stops advancement', async () => {
  vi.useFakeTimers()
  const fetcher = vi.fn().mockResolvedValue(response(receipt('pending')))
  vi.stubGlobal('fetch', fetcher)
  const view = renderHook(({chat}) => useExtensionInstallation(chat), {initialProps: {chat: 'one'}})
  expect(fetcher).not.toHaveBeenCalled()
  await act(async () => { view.result.current.start('Discuss /extensions @demo'); await vi.advanceTimersByTimeAsync(0) })
  expect(fetcher).not.toHaveBeenCalled()
  await act(async () => {
    view.result.current.start('/extensions @demo')
    view.result.current.start('/extensions @demo')
    await vi.advanceTimersByTimeAsync(0)
  })
  expect(fetcher).toHaveBeenCalledTimes(1)
  view.rerender({chat: 'two'})
  await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
  expect(fetcher).toHaveBeenCalledTimes(1)
  expect(view.result.current.state).toBeNull()
})


test('explicit recovery reconciles a lost acknowledgement with the same catalog coordinator', async () => {
  const fetcher = vi.fn().mockRejectedValueOnce(new Error('connection lost'))
    .mockResolvedValueOnce(response(receipt('succeeded')))
  vi.stubGlobal('fetch', fetcher)
  const view = renderHook(() => useExtensionInstallation('chat'))
  await act(async () => view.result.current.start('/extensions @demo install', undefined,
    {chatId: 'chat', requestId: 'turn'}))
  expect(view.result.current.state.state).toBe('reconciliation_required')
  expect(fetcher).toHaveBeenCalledTimes(1)
  await act(async () => view.result.current.resume())
  expect(fetcher).toHaveBeenCalledTimes(2)
  expect(fetcher.mock.calls.every(([url]) => url === '/api/extensions/demo/install-next')).toBe(true)
  expect(view.result.current.state).toMatchObject({state: 'succeeded', chatId: 'chat', requestId: 'turn'})
  await act(async () => view.result.current.resume())
  expect(fetcher).toHaveBeenCalledTimes(2)
  view.unmount()
})

test('recovery cannot resume installation after switching chats', async () => {
  const fetcher = vi.fn().mockRejectedValue(new Error('connection lost'))
  vi.stubGlobal('fetch', fetcher)
  const view = renderHook(({chat}) => useExtensionInstallation(chat), {initialProps: {chat: 'chat'}})
  await act(async () => view.result.current.start('/extensions @demo install', undefined,
    {chatId: 'chat', requestId: 'turn'}))
  view.rerender({chat: 'other'})
  await act(async () => view.result.current.resume())
  expect(fetcher).toHaveBeenCalledTimes(1)
  view.unmount()
})
