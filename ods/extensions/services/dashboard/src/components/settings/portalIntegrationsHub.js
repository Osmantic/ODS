// Pure helpers for the Portal integrations hub: its tiles, what each one's status chip says, and
// which tiles hold unsaved changes. The page (PortalIntegrationsSettings.jsx) draws them.
import { sameDraft } from './portalIntegrationsForm'

// Hub order: what the owner sets up first, then their apps, then the records.
// logo: a brand mark in portalIntegrationLogos.jsx; icon: a generic mark for Portal's own parts.
export const HUB_TILES = [
  { id: 'notifications', title: 'ntfy', about: 'Notifications and reminders', logo: 'ntfy' },
  { id: 'telegram', title: 'Telegram', about: 'Chat with Portal from your phone', logo: 'telegram' },
  { id: 'memory', title: 'Memory', about: 'What Portal remembers about you', icon: 'memory' },
  { id: 'notes', title: 'Obsidian', about: 'Your notes', logo: 'obsidian' },
  { id: 'documents', title: 'Paperless-ngx', about: 'Your documents', logo: 'paperless' },
  { id: 'photos', title: 'Immich', about: 'Your photos', logo: 'immich' },
  { id: 'watch', title: 'changedetection.io', about: 'Watched pages and prices', logo: 'changedetection' },
  { id: 'news', title: 'FreshRSS', about: 'Your news feeds', logo: 'freshrss' },
  { id: 'mcp', title: 'Home Assistant', about: 'And other MCP servers', logo: 'homeAssistant' },
  { id: 'approvals', title: 'Approvals', about: 'Actions waiting for you', icon: 'approvals' },
  { id: 'activity', title: 'Activity', about: 'What Portal did recently', icon: 'activity' },
]

export const HUB_VIEWS = new Set(HUB_TILES.map(tile => tile.id))
const APP_IDS = ['documents', 'photos', 'watch', 'news']

// tone: on | off | setup | problem | waiting | info
const chip = (tone, label) => ({ tone, label })
const OFF = chip('off', 'Off')
const ON = chip('on', 'On')
const SETUP = chip('setup', 'Needs setup')
const plural = (count, one, many) => `${count} ${count === 1 ? one : many}`

function appStatus(app) {
  if (!app?.enabled) return OFF
  return app.hasKey && app.url ? ON : SETUP
}

function mcpStatus(servers) {
  if (!servers.length) return chip('off', 'None yet')
  const on = servers.filter(server => server.enabled).length
  return on ? chip('on', `${on} on`) : OFF
}

// The chip of each tile, from what is saved (not the unsaved draft): the hub tells the owner what
// Portal can do now.
export function tileStatus(id, view) {
  const settings = view.settings
  if (id === 'notifications') {
    const notes = settings.notifications
    if (!notes.enabled) return OFF
    return notes.url && notes.topic ? ON : SETUP
  }
  if (id === 'telegram') {
    const telegram = view.telegram
    if (!telegram?.enabled) return OFF
    if (telegram.lastError) return chip('problem', 'Problem')
    return telegram.hasToken && telegram.ownerId ? ON : SETUP
  }
  if (id === 'memory') {
    const memory = view.memory
    if (!memory?.enabled) return OFF
    if (memory.pending?.length) return chip('waiting', plural(memory.pending.length, 'to review', 'to review'))
    return chip('on', plural(memory.items.length, 'memory', 'memories'))
  }
  if (id === 'notes') {
    if (!settings.notes.enabled) return OFF
    return settings.notes.vaultPath ? ON : SETUP
  }
  if (APP_IDS.includes(id)) return appStatus(settings.apps[id])
  if (id === 'mcp') return mcpStatus(settings.mcp.servers)
  if (id === 'approvals') {
    const count = view.approvals.length
    return count ? chip('waiting', `${count} waiting`) : chip('info', 'Nothing waiting')
  }
  if (id === 'activity') return chip('info', view.activity.length ? plural(Math.min(view.activity.length, 500), 'entry', 'entries') : 'None yet')
  throw new Error(`Unknown Portal integrations tile: ${id}`)
}

// Key order does not matter: a draft rebased onto newer settings may list its fields in another order.
const same = sameDraft
const typedAny = value => Object.values(value || {}).some(item => (item && typeof item === 'object' ? typedAny(item) : Boolean(item)))

// The tiles whose part of the draft differs from the saved settings, or that hold a typed secret.
export function editedTiles(draft, saved, typed) {
  const edited = new Set()
  if (!same(draft.notifications, saved.notifications) || typed.ntfyPassword) edited.add('notifications')
  if (!same(draft.notes, saved.notes)) edited.add('notes')
  for (const id of APP_IDS) if (!same(draft.apps[id], saved.apps[id]) || typed.apps?.[id]) edited.add(id)
  if (!same(draft.servers, saved.servers) || typedAny(typed.mcp) || typedAny(typed.mcpEnv)) edited.add('mcp')
  return edited
}

// The view a Settings link asks for (?view=telegram); anything else is the hub.
export const viewFrom = value => (HUB_VIEWS.has(value) ? value : '')
