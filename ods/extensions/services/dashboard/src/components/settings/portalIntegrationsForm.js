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
  const names = new Map(draft.servers.map(server => [server.id, new Set(server.envNames)]))
  const mcpEnv = Object.fromEntries(Object.entries(typed.mcpEnv || {}).filter(([id]) => names.has(id))
    .map(([id, values]) => [id, Object.fromEntries(Object.entries(values).filter(([name]) => names.get(id).has(name)))]))
  const mcp = Object.fromEntries(Object.entries(typed.mcp).filter(([id]) => names.has(id)))
  return { ...typed, mcp, mcpEnv }
}

// An unsaved credential belongs to the destination that was visible when it was entered.
// A reload or poll may move other edits onto a newly saved destination.
const sameReceiver = (before, after, fields) => before && after && fields.every(key => before[key] === after[key])
const serverReceiverFields = ['kind', 'preset', 'url', 'command', 'workingFolder']

export function credentialsAfterRefresh(typed, before, after) {
  if (!before) return typed
  const previous = new Map(before.servers.map(server => [server.id, server]))
  const unchanged = new Set(after.servers.filter(server => {
    const old = previous.get(server.id)
    return sameReceiver(old, server, serverReceiverFields)
      && sameDraft([...(old.envNames || [])].sort(), [...(server.envNames || [])].sort())
  }).map(server => server.id))
  const keep = values => Object.fromEntries(Object.entries(values).filter(([id]) => unchanged.has(id)))
  return {
    ...typed,
    ntfyPassword: sameReceiver(before.notifications, after.notifications, ['url', 'topic', 'username']) ? typed.ntfyPassword : '',
    mcp: keep(typed.mcp), mcpEnv: keep(typed.mcpEnv),
  }
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

// The saved server's tools still describe the draft only while its address or command is unchanged.
export function savedTools(saved, server) {
  if (!saved || saved.kind !== server.kind || saved.url !== server.url || saved.command !== server.command) return null
  return saved.discoveredAt ? saved.tools : null
}

export function toolEffect(tool) {
  if (tool.readOnly) return 'Only reads'
  return tool.destructive ? 'Can change or delete things' : 'Only adds things'
}

export const sameDraft = (a, b) => JSON.stringify(a) === JSON.stringify(b)

const isRecord = value => Boolean(value) && typeof value === 'object' && !Array.isArray(value)

// One object of the draft: what the owner changed (mine against from) on top of the newer values.
function rebaseRecord(mine, from, latest) {
  const keys = new Set([...Object.keys(latest), ...Object.keys(mine)])
  return Object.fromEntries([...keys].flatMap(key => {
    if (sameDraft(mine[key], from?.[key])) return key in latest ? [[key, latest[key]]] : []
    if (isRecord(mine[key]) && isRecord(latest[key]) && isRecord(from?.[key])) return [[key, rebaseRecord(mine[key], from[key], latest[key])]]
    return [[key, mine[key]]]
  }))
}

// The owner's unsaved changes moved onto settings saved meanwhile elsewhere (all three are drafts):
// what the owner did not touch takes the newer value, so a change made in another window, such as
// a permission turned off, is never put back by a save from this one.
export function rebaseDraft(draft, base, latest) {
  const mine = new Map(draft.servers.map(server => [server.id, server]))
  const from = new Map(base.servers.map(server => [server.id, server]))
  const now = new Set(latest.servers.map(server => server.id))
  const servers = latest.servers.flatMap(server => {
    if (!mine.has(server.id)) return from.has(server.id) ? [] : [server] // Removed here, or added elsewhere.
    if (!from.has(server.id)) return [server] // Both windows added this id. Keep the saved server.
    return [rebaseRecord(mine.get(server.id), from.get(server.id), server)]
  })
  const added = draft.servers.filter(server => !from.has(server.id) && !now.has(server.id))
  return {
    notifications: rebaseRecord(draft.notifications, base.notifications, latest.notifications),
    notes: rebaseRecord(draft.notes, base.notes, latest.notes),
    servers: [...servers, ...added],
  }
}
