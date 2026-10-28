// Pure helpers for Settings → Portal integrations: the editable draft, the save request,
// and the labels the owner reads before allowing a tool.

export const POLICY_LABELS = { off: 'Off', ask: 'Ask first', allow: 'Allowed' }

export const HOME_ASSISTANT = {
  name: 'Home Assistant', kind: 'http', preset: 'home-assistant',
  url: 'http://homeassistant.local:8123/api/mcp',
}

export function draftFrom(settings) {
  return {
    notifications: { ...settings.notifications },
    notes: { ...settings.notes },
    servers: settings.mcp.servers.map(server => ({
      id: server.id, name: server.name, kind: server.kind, preset: server.preset, enabled: server.enabled,
      url: server.url, command: server.command,
      policies: Object.fromEntries(server.tools.map(tool => [tool.name, tool.policy])),
    })),
  }
}

export function settingsFrom(draft) {
  const { enabled, url, topic, username, notifyApprovals } = draft.notifications
  const { enabled: notesOn, vaultPath, write } = draft.notes
  return {
    notifications: { enabled, url, topic, username, notifyApprovals },
    notes: { enabled: notesOn, vaultPath, write },
    mcp: { servers: draft.servers.map(({ id, name, kind, preset, enabled: on, url: address, command, policies }) =>
      ({ id, name, kind, preset, enabled: on, url: address, command, policies })) },
  }
}

// Only typed secrets are sent; an empty field keeps the saved one.
export function secretsFrom(typed) {
  const secrets = {}
  if (typed.ntfyPassword) secrets.ntfyPassword = typed.ntfyPassword
  const tokens = Object.fromEntries(Object.entries(typed.mcp).filter(([, token]) => token))
  if (Object.keys(tokens).length) secrets.mcp = tokens
  return secrets
}

export function newServerId(name, servers) {
  const base = (name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'server').slice(0, 24)
  const taken = new Set(servers.map(server => server.id))
  let id = base
  for (let count = 2; taken.has(id); count++) id = `${base}-${count}`
  return id
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
