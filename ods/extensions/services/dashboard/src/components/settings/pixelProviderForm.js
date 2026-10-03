const idPattern = /^[a-z][a-z0-9_-]{0,63}$/
const control = /[\p{C}]/u
const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key))
const integer = (value, min, max) => Number.isSafeInteger(value) && value >= min && value <= max
const text = (value, max) => typeof value === 'string' && value.trim().length > 0 && value.length <= max && !control.test(value)
const identifier = value => typeof value === 'string' && idPattern.test(value) && !control.test(value)
export const copy = value => JSON.parse(JSON.stringify(value))
export const eligible = (provider, policy) => provider.enabled && (provider.kind !== 'cloud' || policy.allowCloud)

function validUrl(value) {
  if (!text(value, 2048) || /[\s\\@?#]/u.test(value)) return false
  try {
    const url = new URL(value)
    return ['https:', 'http:'].includes(url.protocol) && url.hostname
      && /^(\/[A-Za-z0-9_-]+)*\/v1\/?$/.test(url.pathname)
      && (url.protocol === 'https:' || url.hostname === 'localhost' || url.hostname === '[::1]'
        || /^127\.\d+\.\d+\.\d+$/.test(url.hostname))
  } catch { return false }
}

export function configurationError(doc) {
  if (!exact(doc, ['schemaVersion', 'revision', 'enabled', 'providers', 'roles', 'policy'])
    || doc.schemaVersion !== 1 || !integer(doc.revision, 0, Number.MAX_SAFE_INTEGER)
    || typeof doc.enabled !== 'boolean' || !Array.isArray(doc.providers) || doc.providers.length > 32
    || !exact(doc.roles, ['leader', 'backups', 'advisor', 'handoff'])
    || !exact(doc.policy, ['allowCloud', 'maxAttempts', 'deadlineSeconds'])) return 'Invalid provider configuration.'
  if (typeof doc.policy.allowCloud !== 'boolean' || !integer(doc.policy.maxAttempts, 1, 9)
    || !integer(doc.policy.deadlineSeconds, 1, 3600)) return 'Use 1–9 attempts and a 1–3600 second deadline.'
  const ids = new Set()
  for (const p of doc.providers) {
    if (!exact(p, ['id', 'label', 'kind', 'baseUrl', 'model', 'contextTokens', 'maxOutputTokens',
      'supportsTools', 'supportsVision', 'reasoning', 'hasCredential', 'enabled'])
      || !identifier(p.id) || ids.has(p.id) || !text(p.label, 256) || !text(p.model, 256)
      || !['local', 'ods-peer', 'cloud'].includes(p.kind)
      || ['supportsTools', 'supportsVision', 'reasoning', 'hasCredential', 'enabled'].some(k => typeof p[k] !== 'boolean')) {
      return 'Each provider needs a unique valid ID, label, model, and valid capabilities.'
    }
    ids.add(p.id)
    if (!validUrl(p.baseUrl)) return 'Use an HTTPS base URL ending in /v1, or HTTP on loopback only.'
    if (!integer(p.contextTokens, 256, 10000000) || !integer(p.maxOutputTokens, 1, p.contextTokens)) {
      return 'Token limits must be whole numbers; output cannot exceed the context window.'
    }
    if (p.enabled && p.kind === 'cloud' && !p.hasCredential) return 'An enabled cloud provider requires a key. Disable it before removing its key.'
  }
  const roles = doc.roles
  if (!Array.isArray(roles.backups) || roles.backups.length > 8 || new Set(roles.backups).size !== roles.backups.length
    || roles.backups.some(id => !identifier(id) || !ids.has(id)) || roles.backups.includes(roles.leader)
    || ['leader', 'advisor', 'handoff'].some(role => roles[role] !== null && (!identifier(roles[role]) || !ids.has(roles[role])))) {
    return 'Roles must reference existing providers; backups must be unique and exclude the leader.'
  }
  if (doc.enabled && (!roles.leader || [roles.leader, ...roles.backups, roles.advisor, roles.handoff]
    .filter(Boolean).some(id => !eligible(doc.providers.find(p => p.id === id), doc.policy)))) {
    return 'Enabled routing needs an enabled leader and eligible role providers. Cloud routing requires explicit opt-in.'
  }
  return null
}

export function readConfiguration(payload, revision) {
  if (configurationError(payload?.configuration)
    || (revision !== undefined && payload.configuration.revision !== revision)) throw new Error('invalid-response')
  return copy(payload.configuration)
}

export function prepareSave(draft, snapshot, secrets, removals) {
  const effective = copy(draft)
  const credentialChanges = {}
  for (const provider of effective.providers) {
    const id = provider.id
    const key = secrets[id] || ''
    if (key) {
      if (key.length > 8192 || /[^\x21-\x7e]/.test(key)) throw new Error('Keys must contain 1–8192 printable non-space ASCII characters.')
      credentialChanges[id] = { action: 'set', value: key }
      provider.hasCredential = true
    } else if (removals[id]) {
      credentialChanges[id] = { action: 'remove' }
      provider.hasCredential = false
    }
    const old = snapshot.providers.find(p => p.id === id)
    if (old?.hasCredential && !credentialChanges[id] && (old.baseUrl !== provider.baseUrl || old.kind !== provider.kind)) {
      throw new Error('Changing a keyed provider’s endpoint or kind requires replacing or removing its key.')
    }
  }
  const problem = configurationError(effective)
  if (problem) throw new Error(problem)
  return { expectedRevision: draft.revision, document: copy(draft), credentialChanges }
}

export function createProvider(id, label, providers) {
  if (!identifier(id) || !text(label, 256) || providers.length >= 32 || providers.some(p => p.id === id)) {
    throw new Error('Use a unique lowercase ID (1–64 letters, digits, hyphens or underscores), a label, and at most 32 providers.')
  }
  return { id, label, kind: 'local', baseUrl: 'http://127.0.0.1:8080/v1', model: '',
    contextTokens: 32768, maxOutputTokens: 4096, supportsTools: true,
    supportsVision: false, reasoning: false, hasCredential: false, enabled: false }
}
