import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { createElement } from 'react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import RemoteProvider from './RemoteProvider'

const response = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
})

const statusPayload = {
  status: 'ready',
  routeState: {
    exists: true,
    valid: true,
    enabled: true,
    mode: 'cloud',
    provider: {
      capability: 'openai-compatible',
      baseUrl: 'http://127.0.0.1:8000/v1',
      model: 'qwen/remote:latest',
      transport: 'ssh',
      contextLength: 32768,
      maxTokens: 4096,
      reasoning: false,
    },
    projection: {
      publicModel: 'ods/current',
      gateway: 'litellm-cloud',
      egressBaseUrl: 'http://remote-provider-egress:8091/v1',
      consumerRoute: 'gateway',
    },
    status: {
      proven: true,
      reason: 'provider-handshake-ok',
      lastProbe: {
        schema: 'ods.remote-provider-probe-receipt.v1',
        ok: true,
        verifiedAt: '2026-07-26T00:00:00+00:00',
        endpoint: '/v1/models',
        httpStatus: 200,
        modelCount: 1,
        resolution: { ok: true, addressCount: 0 },
      },
    },
    errors: [],
  },
  activation: {
    valid: true,
    active: true,
    proven: true,
    reason: 'active_and_proven',
    gateway: 'litellm-cloud',
    publicModel: 'ods/current',
    model: 'qwen/remote:latest',
    contextLength: 32768,
    maxTokens: 4096,
    reasoning: false,
    pixel: 'reconciled',
  },
  peer: {
    configured: false,
    ready: false,
    reason: 'missing_peer_token',
    controlBaseUrl: null,
    transport: null,
    token: { configured: false, bytes: 0 },
  },
  sshSupervisor: {
    reachable: true,
    valid: true,
    status: 'running',
    ready: true,
    readyToStart: true,
    reason: 'ready',
    missingSecrets: [],
  },
  egress: {
    reachable: true,
    valid: true,
    ready: true,
    status: 'ok',
    reason: 'ready',
    secret: { configured: true, bytes: 24 },
    resolution: { ok: true, addressCount: 0 },
  },
  capabilities: {
    inference: true,
    odsPeerLifecycle: false,
  },
  availableActions: {
    configure: true,
    test: true,
    enable: false,
    disable: true,
    remove: true,
  },
}

const driftedStatusPayload = {
  ...statusPayload,
  status: 'degraded',
  activation: {
    ...statusPayload.activation,
    valid: false,
    proven: false,
    reason: 'consumer_drift',
    pixel: 'drifted',
  },
  capabilities: {
    ...statusPayload.capabilities,
    inference: false,
  },
  availableActions: {
    ...statusPayload.availableActions,
    enable: true,
  },
}

const peerReadyStatusPayload = {
  ...statusPayload,
  peer: {
    configured: true,
    ready: true,
    reason: 'ready',
    controlBaseUrl: 'http://remote-provider-ssh-tunnel:18092',
    transport: 'ssh',
    token: { configured: true, bytes: 20 },
  },
  capabilities: {
    ...statusPayload.capabilities,
    odsPeerLifecycle: true,
  },
}

const peerModelsPayload = {
  models: [
    {
      id: 'Qwen/Qwen 3.5 9B',
      name: 'Remote Qwen',
      status: 'downloaded',
      size: '5.2 GB',
    },
    {
      id: 'remote-available',
      name: 'Remote Available',
      status: 'available',
      sizeGb: 2.4,
    },
    {
      id: 'remote-loaded',
      name: 'Remote Loaded',
      status: 'loaded',
      size: '4.0 GB',
    },
  ],
  currentModel: 'remote-loaded',
  activationReadyModel: 'remote-loaded',
}

const peerDownloadStatusPayload = {
  status: 'idle',
  active: false,
  isDownloading: false,
}

const peerActiveDownloadStatusPayload = {
  status: 'downloading',
  active: true,
  isDownloading: true,
  model: 'remote-available',
  percent: 42,
}

const probePayload = {
  schema: 'ods.remote-provider-egress-probe.v1',
  ok: true,
  transport: 'ssh',
  probe: {
    schema: 'ods.remote-provider-probe-receipt.v1',
    ok: true,
    verifiedAt: '2026-07-26T00:05:00+00:00',
    endpoint: '/v1/models',
    httpStatus: 200,
    modelCount: 2,
    resolution: { ok: true, addressCount: 0 },
  },
  tunnel: {
    ok: true,
    ready: true,
    status: 'running',
    reason: 'ready',
  },
  routeProof: {
    recorded: true,
    reachable: true,
    schema: 'ods.remote-provider-proof-record.v1',
    status: {
      proven: true,
      reason: 'provider-handshake-ok',
      lastProbe: {
        schema: 'ods.remote-provider-probe-receipt.v1',
        ok: true,
        verifiedAt: '2026-07-26T00:05:00+00:00',
        endpoint: '/v1/models',
        httpStatus: 200,
        modelCount: 2,
        resolution: { ok: true, addressCount: 0 },
      },
    },
  },
}

const configurePlanPayload = {
  schema: 'ods.remote-provider-lifecycle-operation.v1',
  action: 'configure',
  ok: true,
  route: {
    enabled: true,
    provider: {
      capability: 'openai-compatible',
      baseUrl: 'https://gpu.example.test/v1',
      model: 'qwen/remote:latest',
      transport: 'direct',
      contextLength: 32768,
      maxTokens: 4096,
      reasoning: false,
    },
  },
  writes: {
    routingState: true,
    providerSecret: true,
    sshIdentity: false,
    sshKnownHosts: false,
    removesRoutingState: false,
    removesSecrets: false,
  },
  secretRefs: {
    REMOTE_LLM_API_KEY: { present: true, value: '[REDACTED]' },
  },
}

const configureApplyPayload = {
  ...configurePlanPayload,
  applied: true,
  mutated: true,
  rollback: { attempted: false, ok: null },
  activation: {
    active: true,
    proven: true,
    publicModel: 'ods/current',
    model: 'qwen/remote:latest',
    pixel: 'reconciled',
  },
  probe: {
    ok: true,
    endpoint: '/v1/models',
    httpStatus: 200,
    modelCount: 2,
  },
}

const disabledStatusPayload = {
  ...statusPayload,
  status: 'disabled',
  routeState: {
    ...statusPayload.routeState,
    enabled: false,
    provider: null,
    status: { proven: false, reason: 'disabled' },
  },
  capabilities: {
    inference: false,
    odsPeerLifecycle: false,
  },
  availableActions: {
    configure: true,
    test: false,
    disable: false,
    remove: true,
  },
}

const disableApplyPayload = {
  schema: 'ods.remote-provider-lifecycle-operation.v1',
  action: 'disable',
  ok: true,
  applied: true,
  mutated: true,
  rollback: { attempted: false, ok: null },
  route: { enabled: false },
  writes: {
    routingState: true,
    providerSecret: false,
    removesRoutingState: false,
    removesSecrets: false,
  },
  secretRefs: {},
}

const enableApplyPayload = {
  ...disableApplyPayload,
  action: 'enable',
  route: { enabled: true },
}

const removeApplyPayload = {
  ...disableApplyPayload,
  action: 'remove',
  route: { enabled: false },
  writes: {
    routingState: false,
    providerSecret: false,
    removesRoutingState: true,
    removesSecrets: true,
  },
}

async function fillConfigureForm() {
  await screen.findByRole('heading', { name: 'Remote model' })
  fireEvent.change(screen.getByLabelText('API address'), {
    target: { value: 'https://gpu.example.test/v1' },
  })
  fireEvent.change(screen.getByLabelText('Model'), {
    target: { value: 'qwen/remote:latest' },
  })
  fireEvent.change(screen.getByLabelText('API key'), {
    target: { value: 'unit-test-provider-token' },
  })
}

function requestBody(callIndex) {
  return JSON.parse(globalThis.fetch.mock.calls[callIndex][1].body)
}

beforeEach(() => {
  globalThis.fetch = vi.fn()
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

function mockDownloadObservation(readStatus, readModels = () => peerModelsPayload) {
  globalThis.fetch.mockImplementation(async path => {
    if (path === '/api/remote-provider/status') return response(peerReadyStatusPayload)
    if (path === '/api/remote-provider/peer/models') return response(await readModels())
    if (path === '/api/remote-provider/peer/models/download-status') return readStatus()
    throw new Error(`Unexpected request: ${path}`)
  })
}
const downloadObservationReads = () => globalThis.fetch.mock.calls.filter(([path]) => path.endsWith('/download-status')).length

test.each(['complete', 'failed', 'cancelled'])('observes active peer download until %s and stops', async terminal => {
  vi.useFakeTimers()
  let reads = 0
  mockDownloadObservation(() => response(++reads === 1 ? peerActiveDownloadStatusPayload : {
    status: terminal, active: false, isDownloading: false, model: 'remote-available', percent: 100,
  }))
  const view = render(createElement(RemoteProvider))
  await act(async () => {})
  expect(downloadObservationReads()).toBe(1)
  await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
  expect(downloadObservationReads()).toBe(2)
  expect(screen.getByText(new RegExp(`${terminal} - remote-available - 100%`, 'i'))).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /cancel download/i })).toBeDisabled()
  await act(async () => { await vi.advanceTimersByTimeAsync(6000) })
  expect(downloadObservationReads()).toBe(2)
  view.unmount()
})

test('keeps the active snapshot after an observation error and waits for each read', async () => {
  vi.useFakeTimers()
  let reads = 0
  let finish
  mockDownloadObservation(() => {
    reads += 1
    if (reads === 2) return new Promise(resolve => { finish = resolve })
    return response(peerActiveDownloadStatusPayload)
  })
  const view = render(createElement(RemoteProvider))
  await act(async () => {})
  await act(async () => { await vi.advanceTimersByTimeAsync(6000) })
  expect(downloadObservationReads()).toBe(2)
  await act(async () => { finish(response({ detail: 'Peer temporarily unavailable' }, 503)) })
  expect(screen.getByText('Peer temporarily unavailable')).toBeInTheDocument()
  expect(screen.getByText(/downloading - remote-available - 42%/i)).toBeInTheDocument()
  await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
  expect(downloadObservationReads()).toBe(3)
  expect(screen.queryByText('Peer temporarily unavailable')).not.toBeInTheDocument()
  view.unmount()
  await act(async () => { await vi.advanceTimersByTimeAsync(6000) })
  expect(downloadObservationReads()).toBe(3)
})

test('does not observe an idle peer download', async () => {
  vi.useFakeTimers()
  mockDownloadObservation(() => response(peerDownloadStatusPayload))
  const view = render(createElement(RemoteProvider))
  await act(async () => {})
  await act(async () => { await vi.advanceTimersByTimeAsync(6000) })
  expect(downloadObservationReads()).toBe(1)
  view.unmount()
})

test('reads inventory after completion before making the delivered model loadable', async () => {
  vi.useFakeTimers()
  let statusReads = 0
  let published = false
  mockDownloadObservation(() => {
    statusReads += 1
    if (statusReads === 1) return response(peerActiveDownloadStatusPayload)
    published = true
    return response({ status: 'complete', active: false, isDownloading: false })
  }, () => ({
    models: [{ id: 'remote-available', name: 'Remote Available', status: published ? 'downloaded' : 'available' }],
  }))
  const view = render(createElement(RemoteProvider))
  await act(async () => {})
  expect(screen.getByRole('button', { name: 'Load', exact: true })).toBeDisabled()
  await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
  expect(screen.getByRole('button', { name: 'Load', exact: true })).toBeEnabled()
  view.unmount()
})

test('a cancelled download cannot be overwritten by an older pending observation', async () => {
  vi.useFakeTimers()
  let reads = 0
  let finishOld
  let cancelled = false
  globalThis.fetch.mockImplementation(async path => {
    if (path === '/api/remote-provider/status') return response(peerReadyStatusPayload)
    if (path === '/api/remote-provider/peer/models') return response(peerModelsPayload)
    if (path.endsWith('/download/cancel')) {
      cancelled = true
      return response({ status: 'cancelled' })
    }
    if (path.endsWith('/download-status')) {
      reads += 1
      if (reads === 2) return new Promise(resolve => { finishOld = resolve })
      return response(cancelled ? { status: 'cancelled', active: false, isDownloading: false } : peerActiveDownloadStatusPayload)
    }
    throw new Error(`Unexpected request: ${path}`)
  })
  const view = render(createElement(RemoteProvider))
  await act(async () => {})
  await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: /cancel download/i })) })
  expect(screen.getByRole('button', { name: /cancel download/i })).toBeDisabled()
  await act(async () => { finishOld(response(peerActiveDownloadStatusPayload)) })
  expect(screen.getByRole('button', { name: /cancel download/i })).toBeDisabled()
  await act(async () => { await vi.advanceTimersByTimeAsync(6000) })
  expect(downloadObservationReads()).toBe(3)
  view.unmount()
})

test('retries observation when the final inventory read fails without losing active progress', async () => {
  vi.useFakeTimers()
  let statusReads = 0
  let inventoryReads = 0
  mockDownloadObservation(() => response(++statusReads === 1 ? peerActiveDownloadStatusPayload : {
    status: 'complete', active: false, isDownloading: false,
  }), () => {
    inventoryReads += 1
    if (inventoryReads === 3) throw new Error('Final inventory unavailable')
    return { models: [{ id: 'remote-available', name: 'Remote Available', status: inventoryReads >= 4 ? 'downloaded' : 'available' }] }
  })
  const view = render(createElement(RemoteProvider))
  await act(async () => {})
  await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
  expect(screen.getByText('Final inventory unavailable')).toBeInTheDocument()
  expect(screen.getByText(/downloading - remote-available - 42%/i)).toBeInTheDocument()
  await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
  expect(screen.queryByText('Final inventory unavailable')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Load', exact: true })).toBeEnabled()
  view.unmount()
})

test('renders remote provider status and proof receipt', async () => {
  globalThis.fetch.mockResolvedValueOnce(response(statusPayload))

  render(createElement(RemoteProvider))

  expect(await screen.findByRole('heading', { name: 'Remote model' })).toBeInTheDocument()
  expect(screen.getByText('qwen/remote:latest')).toBeInTheDocument()
  expect(screen.getByText('Provider handshake ok')).toBeInTheDocument()
  expect(screen.getByText('2026-07-26T00:00:00+00:00')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /check connection/i })).toBeEnabled()
})

test('compact views keep the connection draft and never apply changes on navigation', async () => {
  globalThis.fetch.mockResolvedValue(response(statusPayload))
  render(createElement(RemoteProvider, { compact: true }))
  await screen.findByRole('button', { name: 'Connection', exact: true })
  expect(screen.queryByRole('heading', { name: 'Egress' })).toBeNull()
  // The tab exists before the status-to-form effect has hydrated the fields.
  // Start this navigation test from a fully loaded connection draft.
  await waitFor(() => expect(screen.getByLabelText('API address')).toHaveValue(statusPayload.routeState.provider.baseUrl))
  fireEvent.change(screen.getByLabelText('API address'), { target: { value: 'https://draft.example/v1' } })
  fireEvent.click(screen.getByRole('button', { name: 'Diagnostics', exact: true }))
  expect(screen.getByRole('heading', { name: 'Egress' })).toBeVisible()
  expect(screen.queryByRole('textbox', { name: 'API address' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Peer models', exact: true }))
  expect(screen.getByRole('heading', { name: 'ODS Peer Models' })).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Connection', exact: true }))
  expect(screen.getByLabelText('API address')).toHaveValue('https://draft.example/v1')
  expect(globalThis.fetch.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false)
})

test('runs configured route probe and shows proof recording result', async () => {
  globalThis.fetch
    .mockResolvedValueOnce(response(statusPayload))
    .mockResolvedValueOnce(response(probePayload))
    .mockResolvedValueOnce(response(statusPayload))

  render(createElement(RemoteProvider))

  fireEvent.click(await screen.findByRole('button', { name: /check connection/i }))

  await waitFor(() => {
    expect(globalThis.fetch.mock.calls.map(call => call[0])).toEqual([
      '/api/remote-provider/status',
      '/api/remote-provider/probe',
      '/api/remote-provider/status',
    ])
  })
  expect(screen.getByText('Connection checked: the API answered, and ODS and Portal use it.')).toBeInTheDocument()
  expect(screen.getByText('2026-07-26T00:05:00+00:00')).toBeInTheDocument()
})

const NOT_SERVING_PROOF = {
  recorded: false,
  reachable: true,
  reason: 'provider_not_serving',
  statusCode: 502,
  providerStatus: 500,
  providerMessage: 'Connection error.',
}
const NOT_SERVING_SUMMARY = 'The API is not answering chats right now. ODS kept its settings, so chat and Portal fail until ' +
  'it is back. Check again later, or turn the remote model off to use the model on this computer. The API answered: ' +
  'HTTP 500: Connection error.'

test('a provider that lists models but serves no chat is named, not reported as busy', async () => {
  const notServing = { ...probePayload, routeProof: NOT_SERVING_PROOF }
  globalThis.fetch
    .mockResolvedValueOnce(response(statusPayload))
    .mockResolvedValueOnce(response(notServing))
    .mockResolvedValueOnce(response(statusPayload))

  render(createElement(RemoteProvider))
  fireEvent.click(await screen.findByRole('button', { name: /check connection/i }))

  expect(await screen.findAllByText(NOT_SERVING_SUMMARY)).toHaveLength(1)
  expect(screen.getByRole('link', { name: /get help on discord/i })).toBeInTheDocument()
  expect(screen.queryByText(/busy with another model change/)).not.toBeInTheDocument()
})

test('connecting to an API that serves no chat says so once, without claiming ODS kept its model', async () => {
  globalThis.fetch
    .mockResolvedValueOnce(response(disabledStatusPayload))
    .mockResolvedValueOnce(response(configureApplyPayload))
    .mockResolvedValueOnce(response({ ...probePayload, routeProof: NOT_SERVING_PROOF }))
    .mockResolvedValueOnce(response(statusPayload))

  render(createElement(RemoteProvider))
  await fillConfigureForm()
  fireEvent.click(screen.getByRole('button', { name: 'Connect', exact: true }))

  expect(await screen.findAllByText(NOT_SERVING_SUMMARY)).toHaveLength(1)
  expect(screen.queryByText(/still uses its current model/)).not.toBeInTheDocument()
})

test('plans direct provider configuration without rendering secret material', async () => {
  globalThis.fetch
    .mockResolvedValueOnce(response(statusPayload))
    .mockResolvedValueOnce(response(configurePlanPayload))

  render(createElement(RemoteProvider))
  await fillConfigureForm()

  fireEvent.click(screen.getByRole('button', { name: /preview changes/i }))

  await waitFor(() => {
    expect(globalThis.fetch).toHaveBeenCalledTimes(2)
  })
  expect(globalThis.fetch.mock.calls[1][0]).toBe('/api/remote-provider/plan')
  expect(requestBody(1)).toEqual({
    action: 'configure',
    provider: {
      transport: 'direct',
      baseUrl: 'https://gpu.example.test/v1',
      model: 'qwen/remote:latest',
      contextLength: 32768,
      maxTokens: 4096,
      reasoning: false,
    },
    secrets: {
      apiKey: 'unit-test-provider-token',
    },
  })
  expect(await screen.findByText('Configure plan ready')).toBeInTheDocument()
  expect(screen.getByText('REMOTE_LLM_API_KEY')).toBeInTheDocument()
  expect(screen.queryByText('unit-test-provider-token')).not.toBeInTheDocument()
})

test('the output limit is a quarter of the context window until the owner sets it', async () => {
  globalThis.fetch
    .mockResolvedValueOnce(response(disabledStatusPayload))
    .mockResolvedValueOnce(response(configurePlanPayload))

  render(createElement(RemoteProvider))
  await fillConfigureForm()
  const context = screen.getByLabelText('Context window')
  const output = screen.getByLabelText('Max output tokens')
  expect(output).toHaveValue(8192)
  fireEvent.change(context, { target: { value: '131072' } })
  expect(output).toHaveValue(32768)

  fireEvent.click(screen.getByRole('button', { name: /preview changes/i }))
  await waitFor(() => {
    expect(globalThis.fetch).toHaveBeenCalledTimes(2)
  })
  expect(requestBody(1).provider).toMatchObject({ contextLength: 131072, maxTokens: 32768 })

  fireEvent.change(output, { target: { value: '16384' } })
  fireEvent.change(context, { target: { value: '65536' } })
  expect(output).toHaveValue(16384)
})

test('a saved route keeps its output limit when the context changes', async () => {
  globalThis.fetch.mockResolvedValueOnce(response(statusPayload))

  render(createElement(RemoteProvider))
  await screen.findByRole('heading', { name: 'Remote model' })
  const output = screen.getByLabelText('Max output tokens')
  // 4096 is a quarter of the saved 32768-token context, as typed by the owner.
  expect(output).toHaveValue(4096)
  fireEvent.change(screen.getByLabelText('Context window'), { target: { value: '131072' } })
  expect(output).toHaveValue(4096)
})

test('applies direct provider configuration and clears the secret input', async () => {
  globalThis.fetch
    .mockResolvedValueOnce(response(statusPayload))
    .mockResolvedValueOnce(response(configureApplyPayload))
    .mockResolvedValueOnce(response(statusPayload))

  render(createElement(RemoteProvider))
  await fillConfigureForm()
  const apiKeyInput = screen.getByLabelText('API key')

  fireEvent.click(screen.getByRole('button', { name: /save without switching/i }))

  await waitFor(() => {
    expect(globalThis.fetch.mock.calls.map(call => call[0])).toEqual([
      '/api/remote-provider/status',
      '/api/remote-provider/apply',
      '/api/remote-provider/status',
    ])
  })
  expect(requestBody(1).secrets.apiKey).toBe('unit-test-provider-token')
  expect(await screen.findByText('Configure applied')).toBeInTheDocument()
  expect(apiKeyInput).toHaveValue('')
  expect(screen.queryByText('unit-test-provider-token')).not.toBeInTheDocument()
})

test('applies disable lifecycle action and refreshes status', async () => {
  globalThis.fetch
    .mockResolvedValueOnce(response(statusPayload))
    .mockResolvedValueOnce(response(disableApplyPayload))
    .mockResolvedValueOnce(response(disabledStatusPayload))

  render(createElement(RemoteProvider))

  fireEvent.click(await screen.findByRole('button', { name: /switch back to the local model/i }))

  await waitFor(() => {
    expect(globalThis.fetch.mock.calls.map(call => call[0])).toEqual([
      '/api/remote-provider/status',
      '/api/remote-provider/apply',
      '/api/remote-provider/status',
    ])
  })
  expect(requestBody(1)).toEqual({ action: 'disable' })
  expect(screen.getByText('Disable applied')).toBeInTheDocument()
})

test('offers one-click reconciliation when the active consumer drifted', async () => {
  globalThis.fetch
    .mockResolvedValueOnce(response(driftedStatusPayload))
    .mockResolvedValueOnce(response(enableApplyPayload))
    .mockResolvedValueOnce(response(statusPayload))

  render(createElement(RemoteProvider))

  expect(await screen.findByText(/ODS and Portal are not using it right now/i)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /^reconnect$/i }))

  await waitFor(() => {
    expect(globalThis.fetch.mock.calls.map(call => call[0])).toEqual([
      '/api/remote-provider/status',
      '/api/remote-provider/apply',
      '/api/remote-provider/status',
    ])
  })
  expect(requestBody(1)).toEqual({ action: 'enable' })
  expect(screen.getByText('Enable applied')).toBeInTheDocument()
})

test('a route an update paused reads Paused, not a fault, and offers Reconnect', async () => {
  // Fleet, laptop: after an installer rerun kept ODS local, Settings said
  // "Needs attention" for a route the update had paused on purpose.
  globalThis.fetch.mockResolvedValueOnce(response({ ...driftedStatusPayload, status: 'paused' }))

  render(createElement(RemoteProvider))

  expect(await screen.findByText(/An update or a mode change paused this API/i)).toBeInTheDocument()
  expect(screen.getAllByText('Paused').length).toBeGreaterThan(0)
  expect(screen.queryByText(/ODS and Portal are not using it right now/i)).toBeNull()
  expect(screen.queryByText('Needs attention')).toBeNull()
  expect(screen.getByRole('button', { name: /^reconnect$/i })).toBeEnabled()
})

test('confirms remove before deleting route state and stored secrets', async () => {
  const confirmSpy = vi.spyOn(window, 'confirm').mockImplementation(() => true)
  globalThis.fetch
    .mockResolvedValueOnce(response(statusPayload))
    .mockResolvedValueOnce(response(removeApplyPayload))
    .mockResolvedValueOnce(response(disabledStatusPayload))

  render(createElement(RemoteProvider))

  fireEvent.click(await screen.findByRole('button', { name: /remove saved api/i }))

  await waitFor(() => {
    expect(globalThis.fetch.mock.calls.map(call => call[0])).toEqual([
      '/api/remote-provider/status',
      '/api/remote-provider/apply',
      '/api/remote-provider/status',
    ])
  })
  expect(confirmSpy).toHaveBeenCalledWith('Remove the saved API and its key? ODS switches back to the model on this computer.')
  expect(requestBody(1)).toEqual({ action: 'remove' })
  expect(screen.getByText('Remove applied')).toBeInTheDocument()
})

test('renders peer model inventory when peer lifecycle is ready', async () => {
  globalThis.fetch
    .mockResolvedValueOnce(response(peerReadyStatusPayload))
    .mockResolvedValueOnce(response(peerModelsPayload))
    .mockResolvedValueOnce(response(peerDownloadStatusPayload))

  render(createElement(RemoteProvider))

  expect(await screen.findByRole('heading', { name: 'Remote model' })).toBeInTheDocument()
  expect(await screen.findByText('Remote Qwen')).toBeInTheDocument()
  expect(screen.getByText('Remote Available')).toBeInTheDocument()
  expect(screen.getByText('Remote Loaded')).toBeInTheDocument()
  expect(screen.getByText('Idle')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /refresh peer models/i })).toBeEnabled()
  expect(globalThis.fetch.mock.calls.map(call => call[0])).toEqual([
    '/api/remote-provider/status',
    '/api/remote-provider/peer/models',
    '/api/remote-provider/peer/models/download-status',
  ])
})

test('loads peer model through encoded proxy endpoint and refreshes inventory', async () => {
  globalThis.fetch
    .mockResolvedValueOnce(response(peerReadyStatusPayload))
    .mockResolvedValueOnce(response(peerModelsPayload))
    .mockResolvedValueOnce(response(peerDownloadStatusPayload))
    .mockResolvedValueOnce(response({ status: 'activated' }))
    .mockResolvedValueOnce(response(peerModelsPayload))
    .mockResolvedValueOnce(response(peerDownloadStatusPayload))

  render(createElement(RemoteProvider))

  await screen.findByText('Remote Qwen')
  fireEvent.click(screen.getAllByRole('button', { name: /^load$/i }).find(button => !button.disabled))

  await waitFor(() => {
    expect(globalThis.fetch).toHaveBeenCalledTimes(6)
  })
  expect(globalThis.fetch.mock.calls[3][0]).toBe('/api/remote-provider/peer/models/Qwen%2FQwen%203.5%209B/load')
  expect(globalThis.fetch.mock.calls[3][1].method).toBe('POST')
  expect(globalThis.fetch.mock.calls.slice(4).map(call => call[0])).toEqual([
    '/api/remote-provider/peer/models',
    '/api/remote-provider/peer/models/download-status',
  ])
})

test('starts and cancels peer model download through proxy endpoints', async () => {
  globalThis.fetch
    .mockResolvedValueOnce(response(peerReadyStatusPayload))
    .mockResolvedValueOnce(response(peerModelsPayload))
    .mockResolvedValueOnce(response(peerActiveDownloadStatusPayload))
    .mockResolvedValueOnce(response({ status: 'download_started' }))
    .mockResolvedValueOnce(response(peerModelsPayload))
    .mockResolvedValueOnce(response(peerActiveDownloadStatusPayload))
    .mockResolvedValueOnce(response({ status: 'cancelled' }))
    .mockResolvedValueOnce(response(peerModelsPayload))
    .mockResolvedValueOnce(response(peerDownloadStatusPayload))

  render(createElement(RemoteProvider))

  expect(await screen.findByText('Downloading - remote-available - 42%')).toBeInTheDocument()
  fireEvent.click(screen.getAllByRole('button', { name: /^download$/i }).find(button => !button.disabled))

  await waitFor(() => {
    expect(globalThis.fetch).toHaveBeenCalledTimes(6)
  })
  expect(globalThis.fetch.mock.calls[3][0]).toBe('/api/remote-provider/peer/models/remote-available/download')
  expect(globalThis.fetch.mock.calls[3][1].method).toBe('POST')

  // Fetch completion precedes the action cleanup that enables this control.
  await waitFor(() => expect(screen.getByRole('button', { name: /cancel download/i })).not.toBeDisabled())
  fireEvent.click(screen.getByRole('button', { name: /cancel download/i }))

  await waitFor(() => {
    expect(globalThis.fetch).toHaveBeenCalledTimes(9)
  })
  expect(globalThis.fetch.mock.calls[6][0]).toBe('/api/remote-provider/peer/models/download/cancel')
  expect(globalThis.fetch.mock.calls[6][1].method).toBe('POST')
})

test('confirms peer model delete before proxying removal', async () => {
  const confirmSpy = vi.spyOn(window, 'confirm').mockImplementation(() => true)
  globalThis.fetch
    .mockResolvedValueOnce(response(peerReadyStatusPayload))
    .mockResolvedValueOnce(response(peerModelsPayload))
    .mockResolvedValueOnce(response(peerDownloadStatusPayload))
    .mockResolvedValueOnce(response({ status: 'deleted' }))
    .mockResolvedValueOnce(response(peerModelsPayload))
    .mockResolvedValueOnce(response(peerDownloadStatusPayload))

  render(createElement(RemoteProvider))

  await screen.findByText('Remote Qwen')
  fireEvent.click(screen.getAllByRole('button', { name: /^delete$/i }).find(button => !button.disabled))

  await waitFor(() => {
    expect(globalThis.fetch).toHaveBeenCalledTimes(6)
  })
  expect(confirmSpy).toHaveBeenCalledWith('Delete Remote Qwen from the remote ODS peer?')
  expect(globalThis.fetch.mock.calls[3][0]).toBe('/api/remote-provider/peer/models/Qwen%2FQwen%203.5%209B')
  expect(globalThis.fetch.mock.calls[3][1].method).toBe('DELETE')
})


test.each(['apply', 'refresh'])('keeps newer edits while configure %s is pending', async pendingStage => {
  let finishApply
  let finishRefresh
  const apply = new Promise(resolve => { finishApply = resolve })
  const refresh = new Promise(resolve => { finishRefresh = resolve })
  globalThis.fetch
    .mockResolvedValueOnce(response(statusPayload))
    .mockReturnValueOnce(apply)
    .mockReturnValueOnce(refresh)

  render(createElement(RemoteProvider))
  await fillConfigureForm()
  fireEvent.click(screen.getByRole('button', { name: 'Save without switching', exact: true }))
  await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledTimes(2))
  if (pendingStage === 'refresh') {
    await act(async () => { finishApply(response(configureApplyPayload)) })
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledTimes(3))
  }

  fireEvent.change(screen.getByLabelText('API address'), { target: { value: 'https://next.example/v1' } })
  fireEvent.change(screen.getByLabelText('API key'), { target: { value: 'next-provider-token' } })
  await act(async () => {
    finishApply(response(configureApplyPayload))
    finishRefresh(response(statusPayload))
  })

  expect(screen.getByLabelText('API address')).toHaveValue('https://next.example/v1')
  expect(screen.getByLabelText('API key')).toHaveValue('next-provider-token')
  expect(screen.getByRole('button', { name: 'Save without switching', exact: true })).toBeEnabled()
  expect(requestBody(1).provider.baseUrl).toBe('https://gpu.example.test/v1')
})

test.each([
  ['truncated JSON', () => new globalThis.Response('{"applied":'), /response could not be read/],
  ['interrupted transfer', () => new globalThis.Response(new globalThis.ReadableStream({
    start(controller) { controller.error(new TypeError('terminated')) },
  })), /response could not be read/],
  ['aborted body', () => new globalThis.Response(new globalThis.ReadableStream({
    start(controller) { controller.error(new globalThis.DOMException('Aborted', 'AbortError')) },
  })), /Request timed out/],
])('preserves the connection draft after a 200 with %s', async (_name, brokenResponse, message) => {
  globalThis.fetch.mockResolvedValue(response(statusPayload))
    .mockResolvedValueOnce(response(statusPayload))
    .mockResolvedValueOnce(brokenResponse())
  render(createElement(RemoteProvider))
  await fillConfigureForm()
  fireEvent.click(screen.getByRole('button', { name: 'Save without switching', exact: true }))

  expect(await screen.findByText(message)).toBeInTheDocument()
  expect(screen.getByLabelText('API address')).toHaveValue('https://gpu.example.test/v1')
  expect(screen.getByLabelText('API key')).toHaveValue('unit-test-provider-token')
  expect(screen.getByRole('button', { name: 'Save without switching', exact: true })).toBeEnabled()
  expect(screen.queryByText('Unknown completed')).not.toBeInTheDocument()
  // An unreadable receipt does not justify another mutation or a success refresh.
  expect(globalThis.fetch).toHaveBeenCalledTimes(2)

  fireEvent.click(screen.getByRole('button', { name: 'Refresh', exact: true }))
  await screen.findByRole('heading', { name: 'Remote model' })
  expect(screen.getByLabelText('API key')).toHaveValue('unit-test-provider-token')
  expect(screen.getByLabelText('API address')).toHaveValue('https://gpu.example.test/v1')
  expect(globalThis.fetch.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1)
})

test('keeps the HTTP status when an error response is not JSON', async () => {
  globalThis.fetch.mockResolvedValueOnce(response(statusPayload))
    .mockResolvedValueOnce(new globalThis.Response('<html>Bad Gateway</html>', { status: 502 }))
  render(createElement(RemoteProvider))
  await fillConfigureForm()
  fireEvent.click(screen.getByRole('button', { name: 'Save without switching', exact: true }))
  expect(await screen.findByText('Request failed (502)')).toBeInTheDocument()
  expect(screen.getByLabelText('API key')).toHaveValue('unit-test-provider-token')
})

test('connects in one step: saves the API, then checks it and switches ODS to it', async () => {
  globalThis.fetch
    .mockResolvedValueOnce(response(disabledStatusPayload))
    .mockResolvedValueOnce(response(configureApplyPayload))
    .mockResolvedValueOnce(response(probePayload))
    .mockResolvedValueOnce(response(statusPayload))

  render(createElement(RemoteProvider))
  await fillConfigureForm()
  fireEvent.click(screen.getByRole('button', { name: 'Connect', exact: true }))

  expect(await screen.findByText('Connected. ODS and Portal now use qwen/remote:latest from gpu.example.test.')).toBeInTheDocument()
  expect(globalThis.fetch.mock.calls.map(call => call[0])).toEqual([
    '/api/remote-provider/status',
    '/api/remote-provider/apply',
    '/api/remote-provider/probe',
    '/api/remote-provider/status',
  ])
  expect(requestBody(1).action).toBe('configure')
  expect(screen.getByLabelText('API key')).toHaveValue('')
  expect(screen.queryByText('unit-test-provider-token')).not.toBeInTheDocument()
})

test('a refused key says so, that ODS kept its model, and where to get help', async () => {
  globalThis.fetch
    .mockResolvedValueOnce(response(disabledStatusPayload))
    .mockResolvedValueOnce(response(configureApplyPayload))
    .mockResolvedValueOnce(response({ detail: { message: 'remote provider probe returned HTTP 401' } }, 401))
    .mockResolvedValueOnce(response(disabledStatusPayload))

  render(createElement(RemoteProvider))
  await fillConfigureForm()
  fireEvent.click(screen.getByRole('button', { name: 'Connect', exact: true }))

  expect(await screen.findByText('The API refused the key. Check the key, then connect again.')).toBeInTheDocument()
  expect(screen.getByText(/ODS still uses its current model\. The API settings were saved/)).toBeInTheDocument()
  expect(screen.getByText('remote provider probe returned HTTP 401')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /get help on discord/i })).toBeInTheDocument()
})

test('a setting the API rejects before saving changes nothing', async () => {
  globalThis.fetch
    .mockResolvedValueOnce(response(disabledStatusPayload))
    .mockResolvedValueOnce(response({ detail: 'Base URL must use https' }, 400))
    .mockResolvedValueOnce(response(disabledStatusPayload))

  render(createElement(RemoteProvider))
  await fillConfigureForm()
  fireEvent.click(screen.getByRole('button', { name: 'Connect', exact: true }))

  expect(await screen.findByText('Base URL must use https')).toBeInTheDocument()
  expect(screen.getByText('Nothing was changed. ODS still uses its current model.')).toBeInTheDocument()
  expect(globalThis.fetch.mock.calls.map(call => call[0])).not.toContain('/api/remote-provider/probe')
})

test('a short switch-over after a check is not reported as a problem', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  try {
    globalThis.fetch
      .mockResolvedValueOnce(response(statusPayload))
      .mockResolvedValueOnce(response(probePayload))
      .mockResolvedValueOnce(response(driftedStatusPayload))
      .mockResolvedValueOnce(response(statusPayload))

    render(createElement(RemoteProvider))
    fireEvent.click(await screen.findByRole('button', { name: /check connection/i }))

    expect(await screen.findByText('Switching ODS and Portal over…')).toBeInTheDocument()
    expect(screen.queryByText(/ODS and Portal are not using it right now/i)).toBeNull()
    await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledTimes(4))
    await waitFor(() => expect(screen.queryByText('Switching ODS and Portal over…')).toBeNull())
    expect(screen.queryByText(/ODS and Portal are not using it right now/i)).toBeNull()
  } finally {
    vi.useRealTimers()
  }
})
