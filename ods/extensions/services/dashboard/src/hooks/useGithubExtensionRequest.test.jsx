import { act, renderHook } from '@testing-library/react'
import useGithubExtensionRequest, { githubExtensionRepository, observeGithubExtension } from './useGithubExtensionRequest'

const command = '/extensions https://github.com/Owner/Repo.git configure for my project'
const identity = { chatId: 'chat', requestId: 'turn' }
const receipt = { schemaVersion: 1, ...identity, repository: 'https://github.com/owner/repo',
  state: 'pending', installationStarted: false }
const response = value => ({ ok: true, json: async () => value })
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

const proposal = { draftId: 'a'.repeat(64), recipeDigest: 'b'.repeat(64), extensionId: 'example' }
const proposed = { ...receipt, proposal }
const observed = {schemaVersion:1,kind:'ods-extension-request-status',...identity,
  extensionId:'example',requestState:'pending',proposalAccepted:true,prepared:true,runtimeStatus:'cli_installed'}

test('observes managed readiness without preparation or installation side effects', async () => {
  const fetcher=vi.fn().mockResolvedValue(response(observed))
  const report=vi.fn()
  await observeGithubExtension(receipt,proposed,new AbortController().signal,report,fetcher)
  expect(fetcher.mock.calls.map(call=>call[0])).toEqual(['/api/extensions/github/requests/status'])
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual(identity)
  expect(report).toHaveBeenLastCalledWith({target:'example',state:'succeeded'})
})

test('waits for proposal and cancellation prevents preparation or installation', async () => {
  vi.useFakeTimers()
  const controller = new AbortController()
  const fetcher = vi.fn().mockResolvedValue(response(receipt))
  const pending = observeGithubExtension(receipt, receipt, controller.signal, vi.fn(), fetcher)
  await vi.advanceTimersByTimeAsync(5000)
  expect(fetcher).toHaveBeenCalledTimes(1)
  controller.abort()
  await pending
  await vi.advanceTimersByTimeAsync(10000)
  expect(fetcher).toHaveBeenCalledTimes(1)
  expect(JSON.parse(fetcher.mock.calls[0][1].body).action).toBe('read')
})

test.each([{chatId:'other'},{extensionId:'other'},{prepared:false},{runtimeStatus:'invented'}])(
  'rejects inconsistent observation without starting a replacement: %j', async change=>{
    const fetcher=vi.fn().mockResolvedValue(response({...observed,...change}))
    await expect(observeGithubExtension(receipt,proposed,new AbortController().signal,vi.fn(),fetcher)).rejects.toThrow()
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(fetcher.mock.calls[0][0]).toBe('/api/extensions/github/requests/status')
  })

test('accepted draft remains observation-only while awaiting agent advancement', async()=>{
  vi.useFakeTimers()
  const controller=new AbortController()
  const fetcher=vi.fn().mockResolvedValue(response({...observed,prepared:false,runtimeStatus:'not_observed'}))
  const report=vi.fn()
  const run=observeGithubExtension(receipt,proposed,controller.signal,report,fetcher)
  await vi.advanceTimersByTimeAsync(0)
  expect(report).toHaveBeenCalledWith({target:undefined,state:'prepared'})
  controller.abort(); await run
  expect(fetcher).toHaveBeenCalledTimes(1)
  expect(fetcher.mock.calls[0][0]).toBe('/api/extensions/github/requests/status')
})

test('parses only explicit repository commands, preserving the repository boundary', () => {
  expect(githubExtensionRepository(command)).toBe('https://github.com/owner/repo')
  for (const action of ['install', 'inspect', 'research']) {
    expect(githubExtensionRepository(`/extensions ${action} https://github.com/Owner/Repo.git`))
      .toBe('https://github.com/owner/repo')
    expect(githubExtensionRepository(`/goal /extensions ${action} https://github.com/Owner/Repo.git`))
      .toBe('https://github.com/owner/repo')
  }
  for (const invalid of ['discuss ' + command, '/extensions @repo', '/extensions https://github.com/o/r/tree/main',
    '/extensions https://github.com/o/r?token=secret', '/extensions https://github.com.evil/o/r',
    '/extensions install http://github.com/o/r', '/extensions install https://github.com/o/r/tree/main']) {
    expect(githubExtensionRepository(invalid)).toBeNull()
  }
})

test('explicit install command registers the exact owner command once, without UI host mutation', async () => {
  const explicit = '/extensions install https://github.com/Owner/Repo.git'
  const fetcher = vi.fn().mockResolvedValue(response(receipt))
  vi.stubGlobal('fetch', fetcher)
  const view = renderHook(() => useGithubExtensionRequest('chat'))
  await act(async () => { view.result.current.start(explicit, identity); await Promise.resolve() })
  expect(fetcher).toHaveBeenCalledTimes(1)
  expect(fetcher.mock.calls[0][0]).toBe('/api/extensions/github/requests')
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ action: 'create', ...identity, command: explicit })
  view.unmount()
})

test('history rendering has no effect; a fresh accepted command registers once and never installs', async () => {
  const fetcher = vi.fn().mockResolvedValue(response(receipt))
  vi.stubGlobal('fetch', fetcher)
  const view = renderHook(() => useGithubExtensionRequest('chat'))
  expect(fetcher).not.toHaveBeenCalled()
  await act(async () => { view.result.current.start(command, identity); await Promise.resolve() })
  await act(async () => { view.result.current.start(command, identity) })
  expect(fetcher).toHaveBeenCalledTimes(1)
  expect(fetcher.mock.calls[0][0]).toBe('/api/extensions/github/requests')
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ action: 'create', ...identity, command })
  view.unmount()
})

test('abort records cancellation before a slow creation response and never revives it', async () => {
  let finish
  const fetcher = vi.fn().mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    .mockResolvedValue(response({ ...receipt, state: 'cancelled' }))
  vi.stubGlobal('fetch', fetcher)
  const view = renderHook(() => useGithubExtensionRequest('chat'))
  const controller = new AbortController()
  act(() => view.result.current.start(command, identity, controller.signal))
  await act(async () => controller.abort())
  expect(JSON.parse(fetcher.mock.calls[1][1].body).action).toBe('cancel')
  await act(async () => finish(response(receipt)))
  expect(fetcher).toHaveBeenCalledTimes(2)
  view.unmount()
})

test('changing chats stops observation without cancelling the durable request', async () => {
  const fetcher = vi.fn().mockResolvedValue(response(receipt))
  vi.stubGlobal('fetch', fetcher)
  const view = renderHook(({ chat }) => useGithubExtensionRequest(chat), { initialProps: { chat: 'chat' } })
  await act(async () => view.result.current.start(command, identity))
  view.rerender({ chat: 'next' })
  expect(fetcher).toHaveBeenCalledTimes(1)
  view.unmount()
  expect(fetcher.mock.calls.some(([, options]) => JSON.parse(options.body).action === 'cancel')).toBe(false)
})


test('ordinary follow-ups preserve the pending GitHub request without creating another scope', async () => {
  const fetcher = vi.fn().mockResolvedValue(response(receipt))
  vi.stubGlobal('fetch', fetcher)
  const view = renderHook(() => useGithubExtensionRequest('chat'))
  await act(async () => view.result.current.start(command, identity))
  await act(async () => view.result.current.start('sim', { ...identity, requestId: 'next' }))
  expect(fetcher).toHaveBeenCalledTimes(1)
  expect(view.result.current.state.state).toBe('researching')
  view.unmount()
})


test('a lost creation acknowledgement preserves the scope and resume reads the same request', async () => {
  const fetcher = vi.fn().mockRejectedValueOnce(new Error('Connection lost'))
    .mockResolvedValue(response(receipt))
  vi.stubGlobal('fetch', fetcher)
  const view = renderHook(() => useGithubExtensionRequest('chat'))
  await act(async () => view.result.current.start(command, identity))
  expect(view.result.current.state.state).toBe('reconciliation_required')
  expect(fetcher).toHaveBeenCalledTimes(1)
  await act(async () => view.result.current.resume())
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ action: 'read', ...identity })
  expect(fetcher.mock.calls.some(([, options]) => JSON.parse(options.body).action === 'cancel')).toBe(false)
  view.unmount()
})
