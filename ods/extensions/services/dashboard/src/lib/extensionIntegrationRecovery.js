import { sha256 } from '@noble/hashes/sha2.js'

const PREFIX = 'ods.extension-integration.v1:'
const ID = /^[A-Za-z0-9_-]{1,128}$/

export function integrationRequestId(record) {
  const bytes = new TextEncoder().encode(JSON.stringify(['extension-integration', record.chatId,
    record.requestId, record.target, record.project]))
  return Array.from(sha256(bytes), byte => byte.toString(16).padStart(2, '0')).join('')
}

export function readIntegrationRecovery(chatId) {
  try {
    if (!ID.test(chatId || '')) return null
    const raw = localStorage.getItem(PREFIX + chatId)
    if (!raw || raw.length > 20000) return null
    const value = JSON.parse(raw)
    if (value?.version !== 1 || value.chatId !== chatId || !ID.test(value.requestId || '') ||
        !/^[a-z0-9][a-z0-9_-]{0,63}$/.test(value.target || '') ||
        !/^Playground\/[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(value.project || '') ||
        typeof value.command !== 'string' || value.command.length > 16384 ||
        !['pending', 'dispatched'].includes(value.phase)) return null
    return value
  } catch { return null }
}

export function saveIntegrationRecovery(value) {
  try {
    localStorage.setItem(PREFIX + value.chatId, JSON.stringify(value))
    const saved = readIntegrationRecovery(value.chatId)
    return saved?.requestId === value.requestId && saved?.phase === value.phase
  } catch { return false }
}

export function readyIntegrationPlan(plan, target) {
  return plan?.schemaVersion === 1 && plan.extensionId === target &&
    Array.isArray(plan.steps) && plan.steps.length > 0 && plan.steps.length <= 128 &&
    plan.steps.at(-1)?.extensionId === target &&
    new Set(plan.steps.map(step => step?.extensionId)).size === plan.steps.length &&
    plan.steps.every(step => /^[a-z0-9][a-z0-9_-]{0,63}$/.test(step?.extensionId || '') &&
      step.action === 'none' && ['enabled', 'cli_installed'].includes(step.status) &&
      Array.isArray(step.missingConfiguration) && step.missingConfiguration.length === 0)
}
