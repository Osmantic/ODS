// Pure helpers for Settings → Portal integrations: the editable draft, the save request,
// and the labels the owner reads before allowing a tool.

export const POLICY_LABELS = { off: 'Off', ask: 'Ask first', allow: 'Allowed' }

export const HOME_ASSISTANT = {
  name: 'Home Assistant', kind: 'http', preset: 'home-assistant',
  url: 'http://homeassistant.local:8123/api/mcp',
}

export const NO_SECRETS = { ntfyPassword: '', mcp: {}, mcpEnv: {} }

export const ENV_NAME = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/

// Where an ntfy "Open approvals" button takes the owner: this Settings page, where they still sign in.
export const approvalLinkFor = origin => `${origin}/settings?section=portal-integrations`

export function draftFrom(settings) {
  const { enabled, url, topic, username, notifyApprovals, approvalLink } = settings.notifications
  return {
    notifications: { enabled, url, topic, username, notifyApprovals, approvalLink: approvalLink || '' },
    notes: { ...settings.notes },
    servers: settings.mcp.servers.map(server => ({
      id: server.id, name: server.name, kind: server.kind, preset: server.preset, enabled: server.enabled,
      url: server.url, command: server.command, workingFolder: server.workingFolder || '', envNames: server.envNames || [],
      policies: Object.fromEntries(server.tools.map(tool => [tool.name, tool.policy])),
    })),
  }
}

export function settingsFrom(draft) {
  const { enabled: notesOn, vaultPath, write } = draft.notes
  return {
    notifications: { ...draft.notifications },
    notes: { enabled: notesOn, vaultPath, write },
    mcp: { servers: draft.servers.map(({ id, name, kind, preset, enabled: on, url: address, command, workingFolder, envNames, policies }) =>
      ({ id, name, kind, preset, enabled: on, url: address, command, workingFolder, envNames, policies })) },
  }
}

// Only typed secrets are sent; an empty field keeps the saved one.
export function secretsFrom(typed) {
  const secrets = {}
  if (typed.ntfyPassword) secrets.ntfyPassword = typed.ntfyPassword
  const tokens = Object.fromEntries(Object.entries(typed.mcp).filter(([, token]) => token))
  if (Object.keys(tokens).length) secrets.mcp = tokens
  const env = Object.fromEntries(Object.entries(typed.mcpEnv || {})
    .map(([id, values]) => [id, Object.fromEntries(Object.entries(values).filter(([, value]) => value))])
    .filter(([, values]) => Object.keys(values).length))
  if (Object.keys(env).length) secrets.mcpEnv = env
  return secrets
}

// Typed values for variables the draft no longer lists are dropped, so a save never names them.
export function typedForDraft(typed, draft) {
  const names = Object.fromEntries(draft.servers.map(server => [server.id, new Set(server.envNames)]))
  const mcpEnv = Object.fromEntries(Object.entries(typed.mcpEnv || {}).filter(([id]) => names[id])
    .map(([id, values]) => [id, Object.fromEntries(Object.entries(values).filter(([name]) => names[id].has(name)))]))
  const mcp = Object.fromEntries(Object.entries(typed.mcp).filter(([id]) => names[id]))
  return { ...typed, mcp, mcpEnv }
}

export function newServerId(name, servers) {
  const base = (name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'server').slice(0, 24)
  const taken = new Set(servers.map(server => server.id))
  let id = base
  for (let count = 2; taken.has(id); count++) id = `${base}-${count}`
  return id
}

// A new server takes its id from its name when first saved, so Portal sees "files" for a
// server named Files; a saved server keeps its id. Returns draft id → saved id.
export function idsForSave(draft, savedIds) {
  const taken = draft.servers.filter(server => savedIds.has(server.id)).map(server => ({ id: server.id }))
  const ids = {}
  for (const server of draft.servers) {
    if (savedIds.has(server.id)) { ids[server.id] = server.id; continue }
    ids[server.id] = newServerId(server.name, taken)
    taken.push({ id: ids[server.id] })
  }
  return ids
}

export function renameServers(settings, secrets, ids) {
  const renamed = values => values && Object.fromEntries(Object.entries(values).map(([id, value]) => [ids[id] || id, value]))
  return {
    settings: { ...settings, mcp: { servers: settings.mcp.servers.map(server => ({ ...server, id: ids[server.id] })) } },
    secrets: { ...secrets, ...(secrets.mcp ? { mcp: renamed(secrets.mcp) } : {}), ...(secrets.mcpEnv ? { mcpEnv: renamed(secrets.mcpEnv) } : {}) },
  }
}

// The saved discovery describes one effective launch, not just a command name.
export function savedTools(saved, server) {
  if (!saved || saved.kind !== server.kind || saved.url !== server.url || saved.command !== server.command
      || (saved.workingFolder || '') !== (server.workingFolder || '')
      || !sameDraft([...(saved.envNames || [])].sort(), [...(server.envNames || [])].sort())) return null
  return saved.discoveredAt ? saved.tools : null
}

export function toolEffect(tool) {
  if (tool.readOnly) return 'Server reports: read-only'
  return tool.destructive ? 'Server reports: may change or delete things' : 'Server reports: only adds things'
}

const stableDraft = value => JSON.stringify(value, (_key, item) => item && typeof item === 'object' && !Array.isArray(item)
  ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item)
export const sameDraft = (a, b) => stableDraft(a) === stableDraft(b)

const fields = (base, current, saved) => Object.fromEntries([...new Set([...Object.keys(current), ...Object.keys(saved)])]
  .flatMap(key => {
    const source = sameDraft(base?.[key], current[key]) ? saved : current
    return Object.hasOwn(source, key) ? [[key, source[key]]] : []
  }))
const remap = (values, ids) => Object.fromEntries(Object.entries(values).map(([id, value]) => [ids[id] || id, value]))

// Rebase only edits made after the submitted/read snapshot. A returned revision
// may acknowledge a save even when its following discovery or test fails.
export function rebaseDraft(base, current, saved, ids = {}) {
  if (!base || !current) return saved
  const renamed = draft => ({ ...draft, servers: draft.servers.map(server => ({ ...server, id: ids[server.id] || server.id })) })
  base = renamed(base); current = renamed(current)
  const previous = new Map(base.servers.map(server => [server.id, server]))
  const received = new Map(saved.servers.map(server => [server.id, server]))
  const present = new Set(current.servers.map(server => server.id))
  return {
    notifications: fields(base.notifications, current.notifications, saved.notifications),
    notes: fields(base.notes, current.notes, saved.notes),
    servers: [...current.servers.map(server => {
      const old = previous.get(server.id), next = received.get(server.id)
      if (!old || !next) return server
      return { ...fields(old, server, next), policies: fields(old.policies, server.policies, next.policies) }
    }), ...saved.servers.filter(server => !present.has(server.id) && !previous.has(server.id))],
  }
}

// Acknowledge only the exact values sent. New typing during a request remains
// unsaved; confirmed secrets are not retransmitted after a later step fails.
export function acknowledgeSecrets(typed, sent, ids = {}) {
  const mcp = remap(typed.mcp, ids), mcpEnv = remap(typed.mcpEnv || {}, ids)
  return {
    ntfyPassword: typed.ntfyPassword === sent.ntfyPassword ? '' : typed.ntfyPassword,
    mcp: Object.fromEntries(Object.entries(mcp).filter(([id, value]) => value !== sent.mcp?.[id])),
    mcpEnv: Object.fromEntries(Object.entries(mcpEnv).map(([id, values]) => [id,
      Object.fromEntries(Object.entries(values).filter(([name, value]) => value !== sent.mcpEnv?.[id]?.[name]))])),
  }
}
