const FOLLOW_UP_STORAGE_KEY = 'ods-settings-follow-up-v1'
const APPLY_PLAN_STORAGE_KEY = 'ods-settings-apply-plan-v1'
const MAX_FOLLOW_UP_ACTIONS = 8

const stringList = (value) => Array.isArray(value)
  ? [...new Set(value.filter(item => typeof item === 'string' && item.length <= 160))].slice(0, 128)
  : []

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

export const normalizeSettingsApplyPlan = (value) => {
  if (!value || typeof value !== 'object') return null
  const services = stringList(value.services)
  const manualKeys = stringList(value.manualKeys)
  const inactiveServices = stringList(value.inactiveServices)
  const changedKeys = stringList(value.changedKeys)
  const postApplyActions = Array.isArray(value.postApplyActions)
    ? value.postApplyActions.slice(0, MAX_FOLLOW_UP_ACTIONS).map(normalizeAction).filter(Boolean)
    : []
  if (services.length + manualKeys.length + inactiveServices.length + postApplyActions.length === 0) return null
  const status = ['ready', 'partial', 'manual', 'staged', 'post-apply'].includes(value.status)
    ? value.status
    : (services.length ? 'ready' : (manualKeys.length ? 'manual' : 'staged'))
  return {
    status,
    supported: services.length > 0,
    services,
    changedKeys,
    manualKeys,
    inactiveServices,
    summary: typeof value.summary === 'string' ? value.summary.slice(0, 1200) : '',
    postApplyActions,
  }
}

export const loadSettingsApplyPlan = (storage) => {
  try {
    const target = storage === undefined ? globalThis.localStorage : storage
    return normalizeSettingsApplyPlan(JSON.parse(target?.getItem(APPLY_PLAN_STORAGE_KEY) || 'null'))
  } catch {
    return null
  }
}

export const saveSettingsApplyPlan = (plan, storage) => {
  const normalized = normalizeSettingsApplyPlan(plan)
  try {
    const target = storage === undefined ? globalThis.localStorage : storage
    if (normalized) target?.setItem(APPLY_PLAN_STORAGE_KEY, JSON.stringify(normalized))
    else target?.removeItem(APPLY_PLAN_STORAGE_KEY)
  } catch {
    // Persistence is best-effort when browser storage is blocked.
  }
  return normalized
}

export const clearSettingsApplyPlan = (storage) => {
  try {
    const target = storage === undefined ? globalThis.localStorage : storage
    target?.removeItem(APPLY_PLAN_STORAGE_KEY)
  } catch {
    // Persistence is best-effort when browser storage is blocked.
  }
}

export const mergeSettingsApplyPlans = (...plans) => {
  const valid = plans.map(normalizeSettingsApplyPlan).filter(Boolean)
  if (valid.length === 0) return null
  const services = [...new Set(valid.flatMap(plan => plan.services))].sort()
  const manualKeys = [...new Set(valid.flatMap(plan => plan.manualKeys))].sort()
  const inactiveServices = [...new Set(valid.flatMap(plan => plan.inactiveServices))].sort()
  const changedKeys = [...new Set(valid.flatMap(plan => plan.changedKeys))].sort()
  const postApplyActions = [...new Map(valid.flatMap(plan => plan.postApplyActions).map(action => [action.id, action])).values()]
  const status = services.length
    ? (manualKeys.length || inactiveServices.length ? 'partial' : 'ready')
    : (manualKeys.length ? 'manual' : (inactiveServices.length ? 'staged' : 'post-apply'))
  return normalizeSettingsApplyPlan({
    status,
    services,
    changedKeys,
    manualKeys,
    inactiveServices,
    postApplyActions,
    summary: [...new Set(valid.map(plan => plan.summary).filter(Boolean))].join(' '),
  })
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
