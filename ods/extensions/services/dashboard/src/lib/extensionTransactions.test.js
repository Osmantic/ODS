import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  approveExtensionTransaction,
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
          validUntil: '2099-09-12T12:15:00Z',
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
      configurationHash: 'e'.repeat(64),
      fields: [],
      configured: false,
      values: {},
      presentConfigKeys: [],
      presentSecretKeys: 'TOKEN',
      appliedDefaultKeys: [],
    })))
    await expect(getExtensionTransactionConfiguration(TX_ID)).rejects.toMatchObject({ code: 'invalid-transaction-configuration' })
  })

  it('rejects a configuration response rebound to another plan hash', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({
      schema: 'ods.assistant-first.transaction-configuration-view.v1',
      transactionId: TX_ID,
      planHash: 'd'.repeat(64),
      schemaHash: 'c'.repeat(64),
      configurationHash: 'e'.repeat(64),
      fields: [],
      configured: false,
      values: {},
      presentConfigKeys: [],
      presentSecretKeys: [],
      appliedDefaultKeys: [],
    })))
    await expect(getExtensionTransactionConfiguration(TX_ID, HASH)).rejects.toMatchObject({ code: 'transaction-plan-hash-mismatch' })
  })

  it('maps only stable API error codes into client failures', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ error: { code: 'missing-configuration', detail: 'do-not-render-this' } }, 409)))
    await expect(getTransactionCapabilities()).rejects.toMatchObject({ code: 'missing-configuration', status: 409 })
  })

  it('submits both reviewed hashes for owner approval and rejects an absent configuration hash', async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({ transactionId: TX_ID, state: 'approved', sequence: 3 }))
    vi.stubGlobal('fetch', fetchMock)
    await approveExtensionTransaction(TX_ID, HASH, 'e'.repeat(64))
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      planHash: HASH,
      configurationHash: 'e'.repeat(64),
    })
    await expect(approveExtensionTransaction(TX_ID, HASH, null)).rejects.toMatchObject({ code: 'invalid-configuration-hash' })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('rejects secret references in the configuration projection', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({
      schema: 'ods.assistant-first.transaction-configuration-view.v1',
      transactionId: TX_ID,
      planHash: HASH,
      schemaHash: 'c'.repeat(64),
      configurationHash: 'e'.repeat(64),
      fields: [],
      configured: true,
      values: {},
      presentConfigKeys: [],
      presentSecretKeys: [],
      appliedDefaultKeys: [],
      secretReference: 'private-pointer',
    })))
    await expect(getExtensionTransactionConfiguration(TX_ID, HASH)).rejects.toMatchObject({ code: 'secret-value-in-configuration-projection' })
  })
})
