const HASH_PATTERN = /^[0-9a-f]{64}$/
const TRANSACTION_PATTERN = /^txn-[0-9a-f]{24}$/

export class ExtensionTransactionError extends Error {
  constructor(code, status = 0) {
    super(code)
    this.name = 'ExtensionTransactionError'
    this.code = code
    this.status = status
  }
}

const fail = (code) => {
  throw new ExtensionTransactionError(code)
}

const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value)

const assertHash = (value, field) => {
  if (typeof value !== 'string' || !HASH_PATTERN.test(value)) fail(`invalid-${field}`)
  return value
}

const assertTransactionId = (value) => {
  if (typeof value !== 'string' || !TRANSACTION_PATTERN.test(value)) fail('invalid-transaction-id')
  return value
}

const responseCode = async response => {
  const body = await response.json().catch(() => null)
  const code = body?.error?.code
  if (typeof code === 'string' && /^[a-z0-9-]{1,96}$/.test(code)) return code
  if (response.status === 403) return 'owner-session-required'
  if (response.status === 404) return 'transactions-unavailable'
  return 'transaction-request-failed'
}

const requestJson = async (path, options = {}, timeoutMs = 15000) => {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(path, {
      credentials: 'same-origin',
      cache: 'no-store',
      ...options,
      signal: controller.signal,
    })
    if (!response.ok) throw new ExtensionTransactionError(await responseCode(response), response.status)
    return await response.json()
  } catch (error) {
    if (error?.name === 'AbortError') throw new ExtensionTransactionError('transaction-request-timeout')
    if (error instanceof ExtensionTransactionError) throw error
    throw new ExtensionTransactionError('transaction-request-unavailable')
  } finally {
    clearTimeout(timer)
  }
}

const postJson = (path, body, timeoutMs) => requestJson(path, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
}, timeoutMs)

const validatePlan = plan => {
  if (!isObject(plan) || plan.schema !== 'ods.assistant-first.plan.v1') fail('invalid-transaction-plan')
  if (!Array.isArray(plan.selectedServices) || !Array.isArray(plan.operations)) fail('invalid-transaction-plan')
  if (!isObject(plan.resourceDelta) || !Array.isArray(plan.definitions)) fail('invalid-transaction-plan')
  return plan
}

const validateConfiguration = (body, transactionId, planHash = null) => {
  if (!isObject(body) || body.schema !== 'ods.assistant-first.transaction-configuration-view.v1') {
    fail('invalid-transaction-configuration')
  }
  if (body.transactionId !== transactionId || !Array.isArray(body.fields) || !isObject(body.values)) {
    fail('invalid-transaction-configuration')
  }
  assertHash(body.planHash, 'plan-hash')
  if (planHash !== null && body.planHash !== planHash) fail('transaction-plan-hash-mismatch')
  assertHash(body.schemaHash, 'schema-hash')
  if (!Array.isArray(body.presentConfigKeys) || !Array.isArray(body.presentSecretKeys)) {
    fail('invalid-transaction-configuration')
  }
  if (body.presentSecretKeys.some(key => Object.hasOwn(body.values, key))) {
    fail('secret-value-in-configuration-projection')
  }
  return body
}

export const randomIdempotencyKey = () => {
  const bytes = new Uint8Array(32)
  if (!globalThis.crypto?.getRandomValues) fail('secure-random-unavailable')
  globalThis.crypto.getRandomValues(bytes)
  return Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('')
}

export const transactionExpiry = (now = new Date()) => {
  const expires = new Date(now.getTime() + 15 * 60 * 1000)
  return expires.toISOString().replace(/\.\d{3}Z$/, 'Z')
}

export const getTransactionCapabilities = async () => {
  const body = await requestJson('/api/extensions/transactions/capabilities')
  if (!isObject(body) || body.schema !== 'ods.assistant-first.transaction-capabilities.v1') {
    fail('invalid-transaction-capabilities')
  }
  if (body.planning !== true || body.configuration !== true || body.execution !== true) {
    fail('incomplete-transaction-capabilities')
  }
  return body
}

export const createExtensionTransaction = async (serviceId, now = new Date()) => {
  if (typeof serviceId !== 'string' || !/^[a-z0-9][a-z0-9-]{0,63}$/.test(serviceId)) {
    fail('invalid-service-id')
  }
  const body = await postJson('/api/extensions/transactions', {
    intent: {
      requestedServices: [serviceId],
      requestedCapabilities: [],
      providerPreferences: {},
      validUntil: transactionExpiry(now),
      missingConfigKeys: [],
      missingSecretKeys: [],
    },
    idempotencyKey: randomIdempotencyKey(),
  })
  if (!isObject(body) || body.schema !== 'ods.assistant-first.transaction-proposal.v1') {
    fail('invalid-transaction-proposal')
  }
  assertTransactionId(body.transactionId)
  assertHash(body.planHash, 'plan-hash')
  if (!isObject(body.envelope) || body.envelope.planHash !== body.planHash) fail('invalid-transaction-proposal')
  validatePlan(body.envelope.plan)
  if (!['planned', 'awaiting_approval'].includes(body.state)) fail('invalid-transaction-state')
  return body
}

export const getExtensionTransaction = async transactionId => {
  assertTransactionId(transactionId)
  const body = await requestJson(`/api/extensions/transactions/${transactionId}`)
  if (!isObject(body) || body.schema !== 'ods.assistant-first.transaction-status.v1') fail('invalid-transaction-status')
  if (body.transactionId !== transactionId) fail('transaction-identity-mismatch')
  assertHash(body.planHash, 'plan-hash')
  validatePlan(body.plan)
  if (typeof body.state !== 'string' || !Array.isArray(body.journal) || !isObject(body.approval)) {
    fail('invalid-transaction-status')
  }
  return body
}

export const getExtensionTransactionConfiguration = async transactionId => {
  assertTransactionId(transactionId)
  const body = await requestJson(`/api/extensions/transactions/${transactionId}/configuration`)
  return validateConfiguration(body, transactionId)
}

export const configureExtensionTransaction = async (transactionId, planHash, schemaHash, values, secretValues) => {
  assertTransactionId(transactionId)
  assertHash(planHash, 'plan-hash')
  assertHash(schemaHash, 'schema-hash')
  const body = await postJson(`/api/extensions/transactions/${transactionId}/configuration`, {
    planHash,
    schemaHash,
    idempotencyKey: randomIdempotencyKey(),
    values,
    secretValues,
  })
  if (body.schemaHash !== schemaHash || body.configured !== true) fail('invalid-transaction-configuration')
  return validateConfiguration(body, transactionId, planHash)
}

export const approveExtensionTransaction = async (transactionId, planHash) => {
  assertTransactionId(transactionId)
  assertHash(planHash, 'plan-hash')
  const body = await postJson(`/api/extensions/transactions/${transactionId}/approval`, { planHash })
  if (!isObject(body) || body.transactionId !== transactionId || body.state !== 'approved' || !Number.isSafeInteger(body.sequence)) {
    fail('invalid-transaction-approval')
  }
  return body
}

export const executeExtensionTransaction = async (transactionId, planHash) => {
  assertTransactionId(transactionId)
  assertHash(planHash, 'plan-hash')
  const body = await postJson(`/api/extensions/transactions/${transactionId}/execute`, { planHash }, 30 * 60 * 1000)
  if (
    !isObject(body)
    || body.transactionId !== transactionId
    || body.planHash !== planHash
    || !['committed', 'rolled_back', 'manual_recovery_required'].includes(body.finalState)
    || !Number.isSafeInteger(body.sequence)
    || !Array.isArray(body.appliedServices)
  ) {
    fail('invalid-transaction-execution')
  }
  return body
}
