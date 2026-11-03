// Pure helpers for Settings → Portal integrations: the editable draft, the save request,
// and the labels the owner reads before allowing a tool.

export const POLICY_LABELS = { off: 'Off', ask: 'Ask first', allow: 'Allowed' }

export const HOME_ASSISTANT = {
  name: 'Home Assistant', kind: 'http', preset: 'home-assistant',
  url: 'http://homeassistant.local:8123/api/mcp',
}

export const NO_SECRETS = { ntfyPassword: '', mcp: {}, mcpEnv: {}, apps: {} }

// The owner's apps, in the order Settings shows them; ids match the integrations service.
export const APPS = {
  documents: { title: 'Documents', product: 'Paperless-ngx', toggle: 'Let Portal search and read my documents',
    about: 'Ask Portal about your scanned letters, bills and contracts.', secret: 'API token',
    where: 'In Paperless-ngx, open your profile (top right) and copy the API auth token.' },
  photos: { title: 'Photos', product: 'Immich', toggle: 'Let Portal search my photos',
    about: 'Find photos by place and date, and by what is in them when Immich machine learning runs.', secret: 'API key',
    where: 'In Immich, open Account settings → API keys and create a key.' },
  watch: { title: 'Watched pages', product: 'changedetection.io', toggle: 'Let Portal watch web pages for me',
    about: 'Get a notification when a page changes or a price drops.', secret: 'API key',
    where: 'In changedetection.io, open Settings → API and copy the API key.' },
  news: { title: 'News', product: 'FreshRSS', toggle: 'Let Portal read my news feeds',
    about: 'Ask for your unread stories, or get a daily digest.', secret: 'API password',
    where: 'In FreshRSS, open Settings → Profile, set an API password, and allow API access under Settings → Authentication.' },
}

export const TASK_NOTICES = { all: 'Finishes or fails', failures: 'Fails', off: 'Never' }

export const browserTimezone = () => Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'

export const ENV_NAME = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/

// Where an ntfy "Open approvals" button takes the owner: this Settings page, where they still sign in.
export const approvalLinkFor = origin => `${origin}/settings?section=portal-integrations`

export function draftFrom(settings) {
  const { enabled, url, topic, username, notifyApprovals, approvalLink, scheduledTasks } = settings.notifications
  return {
    notifications: { enabled, url, topic, username, notifyApprovals, approvalLink: approvalLink || '', scheduledTasks },
    notes: { ...settings.notes },
    servers: settings.mcp.servers.map(server => ({
      id: server.id, name: server.name, kind: server.kind, preset: server.preset, enabled: server.enabled,
      url: server.url, command: server.command, workingFolder: server.workingFolder || '', envNames: server.envNames || [],
      policies: Object.fromEntries(server.tools.map(tool => [tool.name, tool.policy])),
    })),
    apps: appsDraft(settings.apps),
  }
}

// Each app's editable fields, without the service's "hasKey" flags.
function appsDraft(apps) {
  const { documents, photos, watch, news } = apps
  return {
    documents: { enabled: documents.enabled, url: documents.url },
    photos: { enabled: photos.enabled, url: photos.url },
    watch: { enabled: watch.enabled, url: watch.url, changes: watch.changes },
    news: { enabled: news.enabled, url: news.url, username: news.username, digest: { ...news.digest } },
  }
}

export function settingsFrom(draft) {
  const { enabled: notesOn, vaultPath, write } = draft.notes
  return {
    notifications: { ...draft.notifications },
    notes: { enabled: notesOn, vaultPath, write },
    mcp: { servers: draft.servers.map(({ id, name, kind, preset, enabled: on, url: address, command, workingFolder, envNames, policies }) =>
      ({ id, name, kind, preset, enabled: on, url: address, command, workingFolder, envNames, policies })) },
    apps: appsDraft(draft.apps),
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
  const apps = Object.fromEntries(Object.entries(typed.apps || {}).filter(([, value]) => value))
  if (Object.keys(apps).length) secrets.apps = apps
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
  // The apps are listed to Portal as servers with these ids.
  const taken = new Set([...servers.map(server => server.id), ...Object.keys(APPS)])
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
