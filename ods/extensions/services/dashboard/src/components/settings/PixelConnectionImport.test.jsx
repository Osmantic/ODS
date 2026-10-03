import { StrictMode } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import PixelConnectionImport from './PixelConnectionImport'
import { parseBundle, validateProbeResponse, connectionEndpoint } from './pixelConnectionBundle'

const key = 'ods_infer_' + 'b'.repeat(64)
const bundle = () => ({ schemaVersion: 1, kind: 'ods-inference-connection', label: 'Tower',
  baseUrl: 'http://127.0.0.1:40345/v1', model: 'ods/shared', deviceId: 'device-' + 'a'.repeat(16),
  expiresAt: Math.floor(Date.now() / 1000) + 3600, expected: { catalogId: 'glm', runtimeModelId: 'GLM' },
  credential: { apiKey: key }, execution: 'client-owned' })
const result = b => ({ schemaVersion: 1, endpoint: b.baseUrl, deviceId: b.deviceId, expiresAt: b.expiresAt,
  expected: b.expected, metadata: { catalogId: 'glm', routedModel: 'GLM', identitySource: 'ods-verified-route',
    routeSeq: 23, contextLength: 65536, capabilities: { chat: true, tools: false, vision: false, agentViable: true },
    maxOutputTokens: 4096, expiresAt: b.expiresAt, execution: 'client-owned' } })
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }
function setup() {
  const onImport = vi.fn(() => true), onBusyChange = vi.fn(() => true)
  const rendered = render(<StrictMode><PixelConnectionImport providers={[]} disabled={false} onImport={onImport} onBusyChange={onBusyChange} /></StrictMode>)
  const b = bundle()
  fireEvent.change(screen.getByLabelText('Connection bundle (private)'), { target: { value: JSON.stringify(b) } })
  fireEvent.click(screen.getByText('Review endpoint'))
  return { ...rendered, b, onImport, onBusyChange }
}
function confirmAndProbe() {
  fireEvent.click(screen.getByRole('checkbox'))
  fireEvent.click(screen.getByText('Check connection metadata'))
}
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

describe('guided import does not save or activate', () => {
  it('reviews without network, requires confirmation, imports tools:false as a disabled draft', async () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
    const { b, onImport, onBusyChange } = setup()
    expect(fetch).not.toHaveBeenCalled()
    expect(screen.getByText('Check connection metadata')).toBeDisabled()
    fetch.mockResolvedValue({ ok: true, json: async () => result(b) })
    confirmAndProbe()
    expect(screen.getByLabelText('Connection bundle (private)')).toHaveValue('')
    await screen.findByText('Model metadata matches the connection. Inference and tool execution have not been tested.')
    fireEvent.click(screen.getByText('Add to provider draft'))
    expect(onImport).toHaveBeenCalledTimes(1)
    expect(onImport).toHaveBeenCalledWith(expect.objectContaining({ kind: 'ods-peer', enabled: false,
      supportsTools: false, reasoning: false, contextTokens: 65536, maxOutputTokens: 4096, hasCredential: false }), key)
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(fetch.mock.calls[0][0]).toBe('/api/pixel/providers/connection-probe')
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ bundle: JSON.stringify(b), confirmedEndpoint: b.baseUrl })
    expect(onBusyChange.mock.calls).toEqual([[true], [false]])
    expect(screen.queryByText('Add to provider draft')).not.toBeInTheDocument()
  })

  it('keeps its lock through body read, cancellation rejects late success and permits a fresh check', async () => {
    const body = deferred(); const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
    const { b, onImport, onBusyChange } = setup()
    fetch.mockResolvedValue({ ok: true, json: () => body.promise })
    confirmAndProbe()
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1))
    expect(onBusyChange.mock.calls).toEqual([[true]])
    fireEvent.click(screen.getByText('Cancel check'))
    expect(fetch.mock.calls[0][1].signal.aborted).toBe(true)
    await act(async () => body.resolve(result(b)))
    expect(onImport).not.toHaveBeenCalled()
    expect(screen.queryByText('Add to provider draft')).not.toBeInTheDocument()
    expect(onBusyChange.mock.calls).toEqual([[true], [false]])
    expect(screen.getByLabelText('Connection bundle (private)')).toHaveValue('')
  })

  it('unmount releases the exact lock and rejects late results', async () => {
    const response = deferred(); vi.stubGlobal('fetch', vi.fn(() => response.promise))
    const { b, unmount, onImport, onBusyChange } = setup()
    confirmAndProbe(); unmount()
    await act(async () => response.resolve({ ok: true, json: async () => result(b) }))
    expect(onBusyChange.mock.calls).toEqual([[true], [false]])
    expect(onImport).not.toHaveBeenCalled()
  })

  it('never echoes failure details or retains a retryable key', async () => {
    const json = vi.fn(async () => ({ key })); vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, json })))
    const { onImport } = setup(); confirmAndProbe()
    await screen.findByRole('alert')
    expect(json).not.toHaveBeenCalled()
    expect(document.body.textContent).not.toContain(key)
    expect(screen.getByText('Check connection metadata')).toBeDisabled()
    expect(onImport).not.toHaveBeenCalled()
  })

  it('rejects expired and mismatched probe identity, including late expiry', () => {
    const b = bundle(), parsed = parseBundle(JSON.stringify(b))
    for (const change of [r => { r.metadata.routedModel = 'other' }, r => { r.metadata.capabilities.tools = 'true' },
      r => { r.metadata.credential = key }, r => { r.endpoint = 'https://other.example/v1' }]) {
      const r = result(b); change(r); expect(() => validateProbeResponse(r, parsed)).toThrow()
    }
    vi.spyOn(Date, 'now').mockReturnValue((b.expiresAt + 1) * 1000)
    expect(() => parseBundle(JSON.stringify(b))).toThrow()
    expect(() => validateProbeResponse(result(b), parsed)).toThrow()
    vi.restoreAllMocks()
  })

  it.each(['http://192.168.1.2/v1', 'https://key@example.org/v1', 'http://127.1/v1', 'https://example.org/v1?key=x'])('rejects ambiguous or unprotected endpoint %s', endpoint => {
    expect(() => connectionEndpoint(endpoint)).toThrow()
  })
})
