const FOLLOW_UP_STORAGE_KEY = 'ods-settings-follow-up-v1'
const MAX_FOLLOW_UP_ACTIONS = 8

const normalizeAction = (action) => {
  if (!action || typeof action !== 'object') return null
  const id = typeof action.id === 'string' ? action.id.slice(0, 80) : ''
  const title = typeof action.title === 'string' ? action.title.slice(0, 160) : ''
  const message = typeof action.message === 'string' ? action.message.slice(0, 1200) : ''
  return id && title && message ? { id, title, message } : null
}

export const normalizeSettingsFollowUp = (value) => {
  if (!value || typeof value !== 'object' || !Array.isArray(value.postApplyActions)) return null
  const postApplyActions = value.postApplyActions
    .slice(0, MAX_FOLLOW_UP_ACTIONS)
    .map(normalizeAction)
    .filter(Boolean)
  if (postApplyActions.length === 0) return null
  return {
    status: 'post-apply',
    summary: 'Runtime changes were applied. Complete the required follow-up below.',
    postApplyActions,
  }
}

export const loadSettingsFollowUp = (storage) => {
  try {
    const target = storage === undefined ? globalThis.localStorage : storage
    return normalizeSettingsFollowUp(JSON.parse(target?.getItem(FOLLOW_UP_STORAGE_KEY) || 'null'))
  } catch {
    return null
  }
}

export const saveSettingsFollowUp = (plan, storage) => {
  const normalized = normalizeSettingsFollowUp(plan)
  try {
    const target = storage === undefined ? globalThis.localStorage : storage
    if (normalized) target?.setItem(FOLLOW_UP_STORAGE_KEY, JSON.stringify(normalized))
    else target?.removeItem(FOLLOW_UP_STORAGE_KEY)
  } catch {
    // Persistence is best-effort when browser storage is blocked.
  }
  return normalized
}

export const clearSettingsFollowUp = (storage) => {
  try {
    const target = storage === undefined ? globalThis.localStorage : storage
    target?.removeItem(FOLLOW_UP_STORAGE_KEY)
  } catch {
    // Persistence is best-effort when browser storage is blocked.
  }
}

export const settleSettingsApplyPlan = (plan) => {
  const manualKeys = Array.isArray(plan?.manualKeys) ? [...new Set(plan.manualKeys)].sort() : []
  const inactiveServices = Array.isArray(plan?.inactiveServices) ? [...new Set(plan.inactiveServices)].sort() : []
  const followUpPlan = normalizeSettingsFollowUp(plan)
  const remainingSummary = [
    manualKeys.length > 0 ? `A manual stack restart is still required for: ${manualKeys.join(', ')}.` : '',
    inactiveServices.length > 0 ? `Configuration remains staged until these services are enabled: ${inactiveServices.join(', ')}.` : '',
  ].filter(Boolean).join(' ')
  const remainingPlan = manualKeys.length > 0 || inactiveServices.length > 0 ? {
    status: manualKeys.length > 0 ? 'manual' : 'staged',
    supported: false,
    services: [],
    manualKeys,
    inactiveServices,
    postApplyActions: [],
    summary: `Runtime service changes were applied. ${remainingSummary}`,
  } : null
  return { remainingPlan, followUpPlan }
}
