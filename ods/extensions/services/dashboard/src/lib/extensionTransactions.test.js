import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  createExtensionTransaction,
  getExtensionTransactionConfiguration,
  getTransactionCapabilities,
  transactionExpiry,
} from './extensionTransactions'

const HASH = 'a'.repeat(64)
const TX_ID = `txn-${'b'.repeat(24)}`
const response = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('extension transaction client', () => {
  it('requires a fully wired, schema-bound capability response', async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({
      schema: 'ods.assistant-first.transaction-capabilities.v1',
      planning: true,
      configuration: true,
      execution: true,
    }))
    vi.stubGlobal('fetch', fetchMock)
    await expect(getTransactionCapabilities()).resolves.toMatchObject({ execution: true })
    expect(fetchMock).toHaveBeenCalledWith('/api/extensions/transactions/capabilities', expect.objectContaining({
      credentials: 'same-origin',
      cache: 'no-store',
    }))

    fetchMock.mockResolvedValueOnce(response({
      schema: 'ods.assistant-first.transaction-capabilities.v1',
      planning: true,
      configuration: false,
      execution: true,
    }))
    await expect(getTransactionCapabilities()).rejects.toMatchObject({ code: 'incomplete-transaction-capabilities' })
  })

  it('creates only a bounded service intent and uses a short UTC expiry', async () => {
    vi.spyOn(globalThis.crypto, 'getRandomValues').mockImplementation(array => {
      array.fill(15)
      return array
    })
    const fetchMock = vi.fn().mockResolvedValue(response({
      schema: 'ods.assistant-first.transaction-proposal.v1',
      transactionId: TX_ID,
      planHash: HASH,
      state: 'awaiting_approval',
      sequence: 2,
      envelope: {
        planHash: HASH,
        plan: {
          schema: 'ods.assistant-first.plan.v1',
          selectedServices: ['notes'],
          operations: [{ serviceId: 'notes', action: 'install' }],
          definitions: [],
          resourceDelta: {},
        },
      },
    }, 201))
    vi.stubGlobal('fetch', fetchMock)
    await createExtensionTransaction('notes', new Date('2026-09-12T12:00:00Z'))
    const [, options] = fetchMock.mock.calls[0]
    const body = JSON.parse(options.body)
    expect(body).toEqual({
      intent: {
        requestedServices: ['notes'],
        requestedCapabilities: [],
        providerPreferences: {},
        validUntil: '2026-09-12T12:15:00Z',
        missingConfigKeys: [],
        missingSecretKeys: [],
      },
      idempotencyKey: '0f'.repeat(32),
    })
    expect(body.intent).not.toHaveProperty('operations')
    expect(body.intent).not.toHaveProperty('approval')
    expect(transactionExpiry(new Date('2026-09-12T12:00:00Z'))).toBe('2026-09-12T12:15:00Z')
  })

  it('rejects malformed configuration projections before they reach the form', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({
      schema: 'ods.assistant-first.transaction-configuration-view.v1',
      transactionId: TX_ID,
      planHash: HASH,
      schemaHash: 'c'.repeat(64),
      fields: [],
      values: {},
      presentConfigKeys: [],
      presentSecretKeys: 'TOKEN',
    })))
    await expect(getExtensionTransactionConfiguration(TX_ID)).rejects.toMatchObject({ code: 'invalid-transaction-configuration' })
  })

  it('maps only stable API error codes into client failures', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ error: { code: 'missing-configuration', detail: 'do-not-render-this' } }, 409)))
    await expect(getTransactionCapabilities()).rejects.toMatchObject({ code: 'missing-configuration', status: 409 })
  })
})
