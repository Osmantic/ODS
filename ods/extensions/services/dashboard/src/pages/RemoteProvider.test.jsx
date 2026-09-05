import { fireEvent, render, screen, waitFor } from '@testing-library/react'
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
  await screen.findByRole('heading', { name: 'Remote GPU' })
  fireEvent.change(screen.getByLabelText('Base URL'), {
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
  vi.restoreAllMocks()
})

test('renders remote provider status and proof receipt', async () => {
  globalThis.fetch.mockResolvedValueOnce(response(statusPayload))

  render(createElement(RemoteProvider))

  expect(await screen.findByRole('heading', { name: 'Remote GPU' })).toBeInTheDocument()
  expect(screen.getByText('qwen/remote:latest')).toBeInTheDocument()
  expect(screen.getByText('Provider handshake ok')).toBeInTheDocument()
  expect(screen.getByText('2026-07-26T00:00:00+00:00')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /test route/i })).toBeEnabled()
})

test('runs configured route probe and shows proof recording result', async () => {
  globalThis.fetch
    .mockResolvedValueOnce(response(statusPayload))
    .mockResolvedValueOnce(response(probePayload))
    .mockResolvedValueOnce(response(statusPayload))

  render(createElement(RemoteProvider))

  fireEvent.click(await screen.findByRole('button', { name: /test route/i }))

  await waitFor(() => {
    expect(globalThis.fetch.mock.calls.map(call => call[0])).toEqual([
      '/api/remote-provider/status',
      '/api/remote-provider/probe',
      '/api/remote-provider/status',
    ])
  })
  expect(screen.getByText('Route proof recorded')).toBeInTheDocument()
  expect(screen.getByText('2026-07-26T00:05:00+00:00')).toBeInTheDocument()
})

test('plans direct provider configuration without rendering secret material', async () => {
  globalThis.fetch
    .mockResolvedValueOnce(response(statusPayload))
    .mockResolvedValueOnce(response(configurePlanPayload))

  render(createElement(RemoteProvider))
  await fillConfigureForm()

  fireEvent.click(screen.getByRole('button', { name: /plan/i }))

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

test('applies direct provider configuration and clears the secret input', async () => {
  globalThis.fetch
    .mockResolvedValueOnce(response(statusPayload))
    .mockResolvedValueOnce(response(configureApplyPayload))
    .mockResolvedValueOnce(response(statusPayload))

  render(createElement(RemoteProvider))
  await fillConfigureForm()
  const apiKeyInput = screen.getByLabelText('API key')

  fireEvent.click(screen.getByRole('button', { name: /^configure$/i }))

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

  fireEvent.click(await screen.findByRole('button', { name: /^disable$/i }))

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

  expect(await screen.findByText(/ODS and Pixel are not using its exact model contract/i)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /^reconcile route$/i }))

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

test('confirms remove before deleting route state and stored secrets', async () => {
  const confirmSpy = vi.spyOn(window, 'confirm').mockImplementation(() => true)
  globalThis.fetch
    .mockResolvedValueOnce(response(statusPayload))
    .mockResolvedValueOnce(response(removeApplyPayload))
    .mockResolvedValueOnce(response(disabledStatusPayload))

  render(createElement(RemoteProvider))

  fireEvent.click(await screen.findByRole('button', { name: /^remove$/i }))

  await waitFor(() => {
    expect(globalThis.fetch.mock.calls.map(call => call[0])).toEqual([
      '/api/remote-provider/status',
      '/api/remote-provider/apply',
      '/api/remote-provider/status',
    ])
  })
  expect(confirmSpy).toHaveBeenCalledWith('Remove remote GPU route and stored secrets?')
  expect(requestBody(1)).toEqual({ action: 'remove' })
  expect(screen.getByText('Remove applied')).toBeInTheDocument()
})

test('renders peer model inventory when peer lifecycle is ready', async () => {
  globalThis.fetch
    .mockResolvedValueOnce(response(peerReadyStatusPayload))
    .mockResolvedValueOnce(response(peerModelsPayload))
    .mockResolvedValueOnce(response(peerDownloadStatusPayload))

  render(createElement(RemoteProvider))

  expect(await screen.findByRole('heading', { name: 'Remote GPU' })).toBeInTheDocument()
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
